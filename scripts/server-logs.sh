POD=$(kubectl get pods -l app=cypresshub -o jsonpath={.items..metadata.name})
kubectl logs "${POD}" -c server -f
