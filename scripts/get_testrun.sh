CYKUBE_TOKEN=8fdfacc6-3e56-4152-bdac-865b543d197c
http -A bearer -a $CYKUBE_TOKEN get "https://app.cykube.net/api/testrun/$1"
