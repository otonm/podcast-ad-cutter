create a new feed_publisher module that publishes a new feed in output/ with template <feed-title-slug.rss>. the feed contains all the original metadata from the original feed. the feed url however gets updated to an url contructed from base_url setting. the published feed contains episodes_to_keep number of items.

consider the following scenarios:
1. first startup, no processed files present, no feed present in output/: create the new feed, keep all original links in the enclosures.
1.1. an audio file gets processed and created in output/: the related new feed in output/ gets updated to reflect the change (update_feed_item?). - file processing is a future implementation!
2. new run, orignal feed downloaded, new feed already present & original feed has new items: the new item(s) get added to the new feed, with original url. then the new items are processed and if a new audio file is created, the new feed gets updated to reflect the change.

consider also scenarios where episodes_to_keep is more than the number of items in the feed.

output file pattern: output/{feed-title-slug}/{local datetime DD.MM.YYYY}-{episode-title-slug}.{ext}

url pattern: base_url/{feed-title-slug}.rss, for files: base_url/{local datetime DD.MM.YYYY}-{episode-title-slug}.{ext}

pipeline coordinates all data making sure decoupling is intact. no calls to the database from the publisher class.

use all available facilities, including the database, to implement the features.

implement this update and interview me for any needed clarifications
