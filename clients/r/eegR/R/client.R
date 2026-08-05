#' Create an Economic Evidence Graph client
#'
#' @param base_url API origin.
#' @param actor_id Explicit local actor attribution.
#' @param timeout Request timeout in seconds.
#' @return An `eeg_client` object.
#' @export
eeg_client <- function(base_url = "http://127.0.0.1:8000",
                       actor_id = "local-user",
                       timeout = 30) {
  stopifnot(is.character(base_url), length(base_url) == 1L, nzchar(base_url))
  stopifnot(is.character(actor_id), length(actor_id) == 1L, nzchar(actor_id))
  stopifnot(nchar(actor_id) <= 255L, is.numeric(timeout), timeout > 0)
  structure(
    list(
      base_url = sub("/+$", "", base_url),
      actor_id = actor_id,
      timeout = timeout
    ),
    class = "eeg_client"
  )
}

#' Run one literature-search page
#'
#' @param client An `eeg_client`.
#' @param query Search text.
#' @param modes Candidate channels.
#' @param filters Named filter list.
#' @param historical_cutoff Optional list with `at` and strict `mode`.
#' @param limit Page size from 1 through 50.
#' @param cursor Opaque continuation cursor.
#' @param save_run Whether to persist the exact search run.
#' @return A named API response list.
#' @export
eeg_search <- function(client, query,
                       modes = c("lexical", "semantic"),
                       filters = list(),
                       historical_cutoff = NULL,
                       limit = 20L,
                       cursor = NULL,
                       save_run = FALSE) {
  body <- list(
    query = query,
    modes = unname(modes),
    filters = filters,
    historical_cutoff = historical_cutoff,
    collapse_versions = TRUE,
    limit = as.integer(limit),
    cursor = cursor,
    save_run = save_run
  )
  .eeg_request(client, "POST", "/v1/search", body)
}

#' Retrieve all pages for one bounded search
#'
#' @inheritParams eeg_search
#' @param max_pages Client-side safety cap.
#' @return A list of result records with stable identifiers.
#' @export
eeg_search_all <- function(client, query,
                           modes = c("lexical", "semantic"),
                           filters = list(),
                           historical_cutoff = NULL,
                           limit = 20L,
                           save_run = FALSE,
                           max_pages = 100L) {
  results <- list()
  cursor <- NULL
  for (page_number in seq_len(max_pages)) {
    page <- eeg_search(
      client, query, modes, filters, historical_cutoff, limit, cursor,
      save_run && page_number == 1L
    )
    results <- c(results, page$results)
    cursor <- page$next_cursor
    if (is.null(cursor)) break
  }
  if (!is.null(cursor)) stop("search exceeded max_pages before cursor exhaustion")
  results
}

#' Get a canonical work
#' @param work_id Stable work identifier.
#' @inheritParams eeg_search
#' @export
eeg_get_work <- function(client, work_id) {
  .eeg_request(client, "GET", paste0("/v1/works/", work_id))
}

#' Retrieve one policy-gated evidence excerpt
#' @param evidence_unit_id Stable evidence-unit identifier.
#' @inheritParams eeg_search
#' @export
eeg_retrieve_evidence <- function(client, evidence_unit_id) {
  .eeg_request(client, "GET", paste0("/v1/evidence/", evidence_unit_id))
}

#' Create a research project
#' @param name Project name.
#' @param description Project description.
#' @param idempotency_key Stable operation key.
#' @inheritParams eeg_search
#' @export
eeg_create_project <- function(client, name, description = "", idempotency_key) {
  .eeg_request(
    client,
    "POST",
    "/v1/projects",
    list(name = name, description = description, idempotency_key = idempotency_key)
  )
}

#' Get an authorized project handoff
#' @param project_id Stable project identifier.
#' @inheritParams eeg_search
#' @export
eeg_get_project <- function(client, project_id) {
  .eeg_request(client, "GET", paste0("/v1/projects/", project_id))
}

#' Call one of the eight typed agent tools
#' @param tool_name Allowlisted tool name.
#' @param arguments Named tool input list.
#' @inheritParams eeg_search
#' @export
eeg_call_tool <- function(client, tool_name, arguments) {
  allowed <- c(
    "search_literature", "get_work", "retrieve_evidence", "compare_evidence",
    "compare_versions", "trace_citations", "trace_policy_links", "export_project"
  )
  if (!tool_name %in% allowed) stop("unknown Economic Evidence Graph agent tool")
  .eeg_request(client, "POST", paste0("/v1/agent-tools/", tool_name), arguments)
}

#' Compare one authorized evidence table
#' @param table_id Stable evidence-table identifier.
#' @inheritParams eeg_get_project
#' @export
eeg_compare_evidence <- function(client, project_id, table_id) {
  eeg_call_tool(
    client,
    "compare_evidence",
    list(project_id = project_id, table_id = table_id)
  )
}

#' Create a governed project export
#' @param expected_revision Optimistic project revision.
#' @param format Open export format.
#' @inheritParams eeg_compare_evidence
#' @inheritParams eeg_create_project
#' @export
eeg_export_project <- function(client, project_id, table_id, expected_revision,
                               idempotency_key, format = "json") {
  response <- eeg_call_tool(
    client,
    "export_project",
    list(
      project_id = project_id,
      table_id = table_id,
      expected_revision = as.integer(expected_revision),
      idempotency_key = idempotency_key,
      format = format
    )
  )
  response$result
}

#' Download a project export or its manifest
#' @param export_id Stable project-export identifier.
#' @param manifest Download the manifest instead of table data.
#' @inheritParams eeg_search
#' @export
eeg_download_project_export <- function(client, export_id, manifest = FALSE) {
  kind <- if (manifest) "manifest" else "data"
  .eeg_request(
    client,
    "GET",
    paste0("/v1/project-exports/", export_id, "/", kind),
    raw = TRUE
  )
}

.eeg_request <- function(client, method, path, body = NULL, raw = FALSE) {
  stopifnot(inherits(client, "eeg_client"))
  request <- httr2::request(paste0(client$base_url, path))
  request <- httr2::req_method(request, method)
  request <- httr2::req_headers(request, `X-EEG-Actor` = client$actor_id)
  request <- httr2::req_timeout(request, client$timeout)
  request <- httr2::req_error(request, is_error = function(response) FALSE)
  if (!is.null(body)) request <- httr2::req_body_json(request, body, auto_unbox = TRUE)
  response <- httr2::req_perform(request)
  if (httr2::resp_status(response) >= 400L) {
    problem <- tryCatch(
      httr2::resp_body_json(response, simplifyVector = FALSE),
      error = function(error) NULL
    )
    if (is.null(problem)) stop("Economic Evidence Graph returned an invalid error response")
    condition <- structure(
      list(
        message = problem$detail,
        call = NULL,
        status = problem$status,
        code = problem$code,
        request_id = problem$request_id,
        retryable = problem$retryable
      ),
      class = c("eeg_api_error", "error", "condition")
    )
    stop(condition)
  }
  if (raw) return(httr2::resp_body_raw(response))
  httr2::resp_body_json(response, simplifyVector = FALSE)
}
