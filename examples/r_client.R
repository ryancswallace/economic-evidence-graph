# Run after installing the repository package under clients/r/eegR.
library(eegR)

client <- eeg_client(actor_id = "ra-example")
results <- eeg_search_all(
  client,
  "forward guidance inflation expectations",
  limit = 10L,
  max_pages = 3L
)

for (result in results) {
  message(result$work_id, " ", result$document_version_id, " ", result$title)
}
