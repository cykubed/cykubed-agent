http post http://localhost:5000/api/start branch=force-fail sha=f52d0accf0f73b48d9d30ca9f9f101c78c7b68b9 repos=kisanhubcore/kisanhub-webapp

 #docker run -v /projects/secrets/kisanhub-uat-8e82a4304002.json:/cred.json -e GOOGLE_APPLICATION_CREDENTIALS=/cred.json -e COMMIT_SHA=$COMMIT_SHA -e CYPRESS_HUB=192.168.1.126:5000  gcr.io/kisanhub-uat/cypress-runner:latest

