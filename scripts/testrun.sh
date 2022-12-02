CYKUBE_TOKEN=8fdfacc6-3e56-4152-bdac-865b543d197c
http -A bearer -a $CYKUBE_TOKEN post https://app.cykube.net/api/project/1/c11/start

 #docker run -v /projects/secrets/kisanhub-uat-8e82a4304002.json:/cred.json -e GOOGLE_APPLICATION_CREDENTIALS=/cred.json -e COMMIT_SHA=$COMMIT_SHA -e CYPRESS_HUB=192.168.1.126:5000  gcr.io/kisanhub-uat/cypress-runner:latest

