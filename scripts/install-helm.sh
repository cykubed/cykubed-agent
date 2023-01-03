poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t cykube/agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=1da3b1a6-1027-4144-afdd-28849e5b38ce --set-string agentVersion="$1" ./chart



