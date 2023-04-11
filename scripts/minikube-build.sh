eval "$(minikube docker-env)"
docker build  . -t europe-west2-docker.pkg.dev/cykubeapp/cykube/agent:$1





