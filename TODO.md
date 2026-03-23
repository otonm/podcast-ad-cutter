major refactor the pipeline and the program flow. recreate the pipeline from scratch and adapt any other modules as needed.

pipeline:
1. feedparser returns the ParsedFeed object that contains a list of Episode objects -> current behaviour
2. feedpublisher publishes the final feed with the original urls -> current behaviour
now new behaviour:
3. pipeline iterates over the episodes, for each episode:
does the trascription exist? yes -> does the audio exist -> yes -> copy the audio -> update published feed
    |
    no -> does the audio exist? -> yes -> transcribe -> copy -> update feed
            |
            no -> download the audio -> transcribe -> copy -> update feed

the pipeline must thus be reworked from scratch.

all the loops and flow logic live in the pipeline.

each module recieves only one file at a time to process.

analyze this instructions and the codebase and provide a plan on how to rework the code. do not worry about deleting large portions of the code if needed. goal is to achieve the structure and flow as mentioned above.