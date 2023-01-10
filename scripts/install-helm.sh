poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=3af7a1e8-5bbd-4027-8523-a5cfd87b57d1 --set-string agentImage="nickbrookck/cykube-agent:$1" ./chart



