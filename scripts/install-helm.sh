poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=50a03ae3-68d1-442e-ba4c-279078259b53 --set-string agentImage="nickbrookck/cykube-agent:$1" ./chart



