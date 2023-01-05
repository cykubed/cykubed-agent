poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=d5be81e0-da0c-4172-97dc-5d473d75b2ff --set-string agentVersion="$1" --set-string apiUrl=https://cykube.pagekite.me/api ./chart



