#gcloud container clusters get-credentials europe --region europe-west2 --project kisanhub-uat
#gcloud config set project kisanhub-uat
helm upgrade --install cykube -n cykube --create-namespace --set token=e7714eed-ae86-4324-9fae-ad6faf76cfe6 --set agentName=GKE-KH --set tag="$1" --set apiUrl="https://api.cykube.net" ./chart
kubectl config set-context --current --namespace=cykube
kubectl rollout status statefulsets fs
