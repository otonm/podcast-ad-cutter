use superpowers to debug or rather refactor for this edge case as well.

the rss feed gets downloaded and the episode parsed -> a new episode is found -> the download fails (a 404 error, some other network error, disk full, etc.).

what happens now?

the processing of the episode stops and it gets skipped. since no file was and can be downloaded, no file is in cache.

the other episodes get processed, are probably already present and are correctly skipped.

in the end, the episodes in output are trimmed, since a new episode was found at the very beginnging. BUT since no episode was downloaded or processed, at the end there is an episode missing in output.