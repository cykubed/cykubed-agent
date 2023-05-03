export REDIS_PASSWORD=$(kubectl get secret cykubed-redis --namespace cykubed -o jsonpath="{.data.redis-password}" | base64 -d)
redis-cli -p 6380 -a "$REDIS_PASSWORD"
