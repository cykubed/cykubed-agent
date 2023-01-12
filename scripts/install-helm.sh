poetry export -o requirements.txt
eval "$(minikube docker-env)"
docker build . -t nickbrookck/cykube-agent:$1
helm upgrade --install cykube -n cykube --create-namespace --set-string token=31fd2e71-af73-4e18-b1c6-b20f4fd8df77 --set-string agentImage="nickbrookck/cykube-agent:$1" ./chart



