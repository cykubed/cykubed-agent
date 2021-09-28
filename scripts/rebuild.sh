eval $(minikube docker-env)
docker build . -t nickbrookdocker/cypresshub:1.2
kubectl rollout restart deployment/cypresshub
kubectl rollout status deployment/cypresshub -w
sleep 10
POD=$(kubectl get pods -l app=cypresshub -o jsonpath={.items..metadata.name})
kubectl logs "${POD}" -c worker -f

