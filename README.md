# mik8s

POC de GitOps en cluster local kind con ArgoCD + Crossplane para aprovisionar Redis y Valkey via Claims.

## Objetivo

- Levantar un cluster local desde cero.
- Instalar ArgoCD en modo local (HTTP/insecure) para operar por localhost.
- Instalar Crossplane via ArgoCD.
- Exponer APIs de plataforma con Crossplane (`RedisClaim`, `ValkeyClaim`).
- Desplegar una app demo que consume Redis dentro del cluster.

## Estructura actual

```text
crossplane/
	crossplane-bootstrap.yaml

infrastructure/
	crossplane-setup/
		provider-k8s.yaml
		provider-config-k8s.yaml
		rbac-provider-k8s.yaml
		definition-valkey.yaml
		composition-valkey.yaml
		definition-redis.yaml
		composition-redis.yaml
	instances/
		valkey/
			application.yaml
			manifests/valkey-poc.yaml
		redis/
			application.yaml
			manifests/redis-poc.yaml
		redis-demo/
			application.yaml
			app/
				Dockerfile
				requirements.txt
				app.py
			manifests/
				deployment.yaml
				service.yaml
		valkey-demo/
			application.yaml
			app/
				Dockerfile
				requirements.txt
				app.py
			manifests/
				deployment.yaml
				service.yaml
```

## Prerrequisitos

- `kind`
- `kubectl`
- `git`

Opcional:

- `argocd` CLI
- `redis-cli`

## 1) Crear cluster kind

```bash
kind create cluster --name mik8s
kubectl cluster-info --context kind-mik8s
```

## 2) Instalar ArgoCD

```bash
kubectl create namespace argocd
kubectl apply -n argocd -f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
kubectl wait --for=condition=available deployment/argocd-server -n argocd --timeout=180s
```

## 3) Configurar ArgoCD en modo HTTP local

```bash
kubectl patch configmap argocd-cmd-params-cm -n argocd \
	--type merge -p '{"data":{"server.insecure":"true"}}'
kubectl rollout restart deployment argocd-server -n argocd
kubectl rollout status deployment argocd-server -n argocd
```

Port-forward para UI:

```bash
kubectl port-forward svc/argocd-server -n argocd 8080:80
```

Acceso:

- URL: `http://localhost:8080`
- User: `admin`
- Password inicial:

```bash
kubectl -n argocd get secret argocd-initial-admin-secret \
	-o jsonpath="{.data.password}" | base64 -d && echo
```

## 4) Registrar credenciales de repo Git en ArgoCD

```bash
kubectl apply -f argocd_secret_creds.yaml
```

## 5) Bootstrap de Crossplane via ArgoCD

Aplicar App de ArgoCD que instala Crossplane por Helm:

```bash
kubectl apply -f crossplane/crossplane-bootstrap.yaml
```

Aplicar App de ArgoCD que instala provider, XRDs y compositions:

```bash
kubectl apply -f crossplane-setup-app.yaml
```

## 6) Aplicar apps de instancias (claims)

```bash
kubectl apply -f infrastructure/instances/valkey/application.yaml
kubectl apply -f infrastructure/instances/redis/application.yaml
```

## 7) Build de imagenes demo (sin pip install en runtime)

```bash
docker build -t redis-demo-app:local infrastructure/instances/redis-demo/app
kind load docker-image redis-demo-app:local --name mik8s

docker build -t valkey-demo-app:local infrastructure/instances/valkey-demo/app
kind load docker-image valkey-demo-app:local --name mik8s
```

## 8) Apps demo consumiendo Redis y Valkey

```bash
kubectl apply -f infrastructure/instances/redis-demo/application.yaml
kubectl apply -f infrastructure/instances/valkey-demo/application.yaml
```

## Verificaciones recomendadas

```bash
kubectl get applications -n argocd
kubectl get valkeyclaims,redisclaims -n default
kubectl get objects -n crossplane-system
kubectl get pods,svc,pvc -n default
```

Prueba conectividad a Redis:

```bash
kubectl run redis-test --rm -it -n default \
	--image=redis:7 --restart=Never -- \
	redis-cli -h my-redis-db-svc -p 6379 ping
```

## Cambios importantes realizados en esta POC

### A) Nombres estables en Services, Deployments y PVCs (Crossplane)

Problema original:

- Crossplane generaba nombres derivados del XR (`metadata.name`) con sufijos aleatorios.
- Ejemplo: `my-redis-db-r99k4-svc`.
- Eso complica el consumo desde apps.

Solucion aplicada:

- En `composition-redis.yaml` y `composition-valkey.yaml` se cambio el naming para usar `spec.claimRef.name` en vez de `metadata.name`.
- Resultado estable:
	- Redis Service: `my-redis-db-svc`
	- Valkey Service: `my-valkey-db-svc`

Adicionalmente:

- Se patcharon labels/selectors con el nombre del claim para evitar colisiones entre multiples instancias.

### B) Error de ArgoCD/Crossplane por orden de aplicacion

Problema original:

- ArgoCD intentaba aplicar `ProviderConfig` antes de que el CRD estuviera disponible.
- Error tipico: `could not find ... ProviderConfig`.

Solucion aplicada:

- Se agregaron `argocd.argoproj.io/sync-wave` para forzar orden:
	- Wave 0: `Provider`
	- Wave 1: `ProviderConfig` y `ClusterRoleBinding`
	- Wave 2: `XRDs` y `Compositions`
- En `provider-config-k8s.yaml` se agrego:
	- `argocd.argoproj.io/sync-options: SkipDryRunOnMissingResource=true`

### C) Error de Service selector en composiciones

Problema original:

- Se uso `spec.selector.matchLabels` en un `Service` (incorrecto para `v1/Service`).
- Error: `cannot unmarshal object into ... ServiceSpec.spec.selector of type string`.

Solucion aplicada:

- Se corrigio a formato valido:

```yaml
spec:
	selector:
		app: <valor>
```

### D) ArgoCD lento/inestable: ApplicationSet controller en crash loop

Problema original:

- Faltaba el CRD `applicationsets.argoproj.io`.
- `argocd-applicationset-controller` reiniciaba continuamente.

Solucion aplicada:

- Se re-aplico ArgoCD con server-side apply para evitar limite de anotaciones en CRDs:

```bash
kubectl apply --server-side --force-conflicts \
	-f https://raw.githubusercontent.com/argoproj/argo-cd/stable/manifests/install.yaml
```

- Se validaron rollouts y estado `Running` de todos los pods de ArgoCD.

### E) Recursos para componentes principales de ArgoCD

Se agregaron requests/limits a componentes criticos para mejorar estabilidad en kind:

- `argocd-application-controller`
- `argocd-repo-server`
- `argocd-server`
- `argocd-applicationset-controller`

## Notas de operacion

- Si cambias composiciones (naming/selectors), puede ser necesario recrear claims para re-generar recursos manejados:

```bash
kubectl delete redisclaim my-redis-db -n default
kubectl delete valkeyclaim my-valkey-db -n default
```

- Si quedan Services viejos con sufijo aleatorio, eliminarlos manualmente tras la migracion.

## Proximos pasos sugeridos

- Publicar `redis-demo-app` y `valkey-demo-app` en un registry compartido (GHCR, ECR, Docker Hub) para evitar `kind load` en nuevos nodos.
- Versionar las imagenes con tags semanticos (`v1`, `v1.1.0`) en lugar de `:local`.
