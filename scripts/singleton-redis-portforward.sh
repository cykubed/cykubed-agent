export REDIS_PASSWORD=$(kubectl get secret cykubed-redis --namespace cykubed -o jsonpath="{.data.redis-password}" | base64 -d)
echo "Redis password=$REDIS_PASSWORD"
kubectl port-forward --namespace cykubed svc/cykubed-redis-master 6380:6379

