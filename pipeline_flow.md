flowchart TD
    Fetch(Fetch podcast feed)-->ExistingCheck(Does a feed file already exist in output/?)

    ExistingCheck-->|Yes|NewItemsCheck(Are there new items?)
    ExistingCheck-->|No|WriteNew(Create a new feed with original URLs)-->ForEach(For each item)

    NewItemsCheck-->|Yes|ForEach-->FinalFileCheck(Does the final file exist?)
    NewItemsCheck-->|No|Stop

    FinalFileCheck-->|Yes|UpdateFeed(Update the final feed)
    FinalFileCheck-->|No|AdSegmentsCheck(Do ad segments exist?)    
    
    AdSegmentsCheck-->|Yes|Export(Edit & Export the file)-->FinalFileCheck
    AdSegmentsCheck-->|No|TopicCheck(Does the topic exist?)

    TopicCheck-->|Yes|DetectAds(Detect ads)-->AdSegmentsCheck
    TopicCheck-->|No|TrascriptionCheck(Does the trascript exist?)

    TrascriptionCheck-->|Yes|TopicExtract(Extract the topic)-->TopicCheck
    TrascriptionCheck-->|No|Preprocess(Download & preprocess file)-->Trascribe(Transcribe the file)-->TrascriptionCheck
