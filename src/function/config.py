'''
This module contains the configuration for the function app.
'''
# Configuration for texthook

# The threshold for considering sentences as stable
# default is 3, meaning a sentence must be observed 3 times to be considered stable
STABLE_THRESHOLD = 3  

# The maximum number of recent saved sentences retained in session memory.
# This is not a global deduplication window: later repetitions must survive.
# default is 50
MAX_SAVED_SENTENCES = 50  

# Minimum length for an unfinished sentence to be saved while still visible.
# Short complete sentences are saved too; pending text is flushed on stop/scroll.
MIN_LENGTH=10

# Minimum similarity for updating a saved, extending tail within 3 seconds.
# Distinct numbers and separate occurrences are not merged.
# default is 0.85, meaning sentences with similarity above 0.85 will be considered similar
SIMILARITY=0.85
