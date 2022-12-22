poetry export -o requirements.txt
docker build . -t cykube/agent:$1

