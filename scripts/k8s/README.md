# Kubernetes Deployment

K8s部署配置，支持水平扩展（HPA）和多副本。

## 前置要求

- Kubernetes集群 (v1.22+)
- kubectl CLI工具
- Docker镜像仓库访问权限

## 部署步骤

### 1. 构建并推送Docker镜像

```bash
# Build VAD service image
cd /path/to/vixio
docker build -t your-registry/vixio-vad-service:latest -f services/vad_service/Dockerfile .

# Push to registry
docker push your-registry/vixio-vad-service:latest
```

### 2. 更新镜像地址

编辑 `vad-service.yaml`，将 `your-registry/vixio-vad-service:latest` 替换为实际的镜像地址。

### 3. 部署到K8s

```bash
# Apply all manifests
kubectl apply -f k8s/

# Or apply individually
kubectl apply -f k8s/vad-service.yaml
```

### 4. 验证部署

```bash
# Check pods
kubectl get pods -n vixio

# Check service
kubectl get svc -n vixio

# Check HPA
kubectl get hpa -n vixio

# View logs
kubectl logs -n vixio -l app=vad-service -f
```

## 扩容配置

### HPA（水平Pod自动扩容）

配置位于 `vad-service.yaml` 中的 HorizontalPodAutoscaler:

- **minReplicas**: 2（最小副本数）
- **maxReplicas**: 10（最大副本数）
- **targetCPUUtilization**: 70%（目标CPU利用率）

### 手动扩容

```bash
# Scale manually (overrides HPA temporarily)
kubectl scale deployment vad-service -n vixio --replicas=5

# HPA will eventually adjust back based on metrics
```

### 资源限制

每个Pod的资源配置:

- **requests**: CPU 200m, Memory 150Mi
- **limits**: CPU 500m, Memory 200Mi

根据实际负载调整这些值。

## 监控

### 查看HPA状态

```bash
kubectl get hpa vad-hpa -n vixio -w
```

### 查看Pod指标

```bash
kubectl top pods -n vixio
```

### 查看事件

```bash
kubectl get events -n vixio --sort-by='.lastTimestamp'
```

## 故障排查

### Pod不启动

```bash
# Describe pod
kubectl describe pod <pod-name> -n vixio

# Check logs
kubectl logs <pod-name> -n vixio

# Check events
kubectl get events -n vixio | grep <pod-name>
```

### HPA不工作

```bash
# Check metrics-server
kubectl get deployment metrics-server -n kube-system

# Check HPA status
kubectl describe hpa vad-hpa -n vixio
```

### 服务连接失败

```bash
# Test service from another pod
kubectl run -it --rm debug --image=busybox --restart=Never -n vixio -- sh
# Inside pod:
nc -v vad-service 50051
```

## 清理

```bash
# Delete all resources
kubectl delete -f k8s/

# Or delete namespace (removes everything)
kubectl delete namespace vixio
```

## 生产建议

1. **Resource Quotas**: 为namespace设置资源配额
2. **Network Policies**: 配置网络策略限制流量
3. **Pod Disruption Budgets**: 配置PDB保证可用性
4. **Monitoring**: 集成Prometheus/Grafana监控
5. **Logging**: 配置EFK/ELK日志收集
6. **Secrets**: 使用K8s Secrets管理敏感信息

## 架构说明

### 负载均衡

K8s Service自动在多个Pod之间进行gRPC负载均衡。

### 并发模型

- **VAD服务**: VAD-cycle锁（START→END期间持有锁）
- **会话隔离**: 每个会话独立的VAD状态
- **多副本**: K8s自动在多个Pod之间分配请求

### 扩容策略

HPA基于CPU利用率自动扩容:
- CPU > 70%: 扩容（最多100%增长）
- CPU < 70%: 缩容（最多50%减少）
- 稳定窗口: 扩容60s，缩容300s

