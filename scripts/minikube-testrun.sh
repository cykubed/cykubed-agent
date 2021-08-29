SHA=b6d6ac671a4dbce5d97071f0673bad7fcba059c6
http post `minikube service --url cypresshub`/api/start branch=PH-471-force-fail sha=$SHA repos=kisanhubcore/kisanhub-webapp

 #docker run -v /projects/secrets/kisanhub-uat-8e82a4304002.json:/cred.json -e GOOGLE_APPLICATION_CREDENTIALS=/cred.json -e COMMIT_SHA=$COMMIT_SHA -e CYPRESS_HUB=192.168.1.126:5000  gcr.io/kisanhub-uat/cypress-runner:latest

