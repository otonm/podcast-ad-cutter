create the next module:
an audio_preprocessor module that takes the downloaded file in the cache and creates a mono version with ffmpeg (aac m4a, 32kbps, no metadata). 
file name: cache/{guid}.mono.m4a.
it is triggered after audioprober.
coordination by the pipeline.
no checks for existance or similar.
separate ffmpeg into a separate module ffmpeg.py that can be reused by another module in the future and accepts arbitrary ffmpeg arguments as a list.
add a callback (similar to episodedownloader) the reports back the progress of ffmpeg. refer to https://github.com/slhck/ffmpeg-progress-yield for an example on how to implement it.