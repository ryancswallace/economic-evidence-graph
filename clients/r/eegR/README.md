# eegR

`eegR` is the thin R client for the versioned Economic Evidence Graph API. It
uses the same stable identifiers, project authorization header, bounded agent
tools, opaque search cursors, and governed export path as the Python client.

```r
client <- eeg_client(actor_id = "ra-example")
results <- eeg_search_all(client, "monetary policy inflation", max_pages = 2)
```

The package depends only on the open-source `httr2` HTTP/JSON library. Local
actor attribution is not production authentication.
