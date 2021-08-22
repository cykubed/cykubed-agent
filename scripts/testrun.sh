SHA=edd890b1dd653871f229b30ee5294cec394aa6f7
http post http://localhost:5000/api/start branch=force-fail sha=$SHA repos=kisanhubcore/kisanhub-webapp

 #docker run -v /projects/secrets/kisanhub-uat-8e82a4304002.json:/cred.json -e GOOGLE_APPLICATION_CREDENTIALS=/cred.json -e COMMIT_SHA=$COMMIT_SHA -e CYPRESS_HUB=192.168.1.126:5000  gcr.io/kisanhub-uat/cypress-runner:latest

