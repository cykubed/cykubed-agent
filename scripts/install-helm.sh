poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t cykube/agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=19bb4c8a-40a0-4ceb-8a73-ab9f712a6598 --set-string agentVersion="$1" ./chart



