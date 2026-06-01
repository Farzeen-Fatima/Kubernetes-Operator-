# Makefile for WebPage Kubernetes Operator

# Variables
IMAGE_NAME ?= webpage-operator
IMAGE_TAG ?= latest
REGISTRY ?= docker.io/yourusername
FULL_IMAGE = $(REGISTRY)/$(IMAGE_NAME):$(IMAGE_TAG)
NAMESPACE ?= default

.PHONY: help
help: ## Display this help message
	@echo "WebPage Kubernetes Operator - Available Commands:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

.PHONY: install-deps
install-deps: ## Install Python dependencies locally
	pip install -r requirements.txt

.PHONY: build
build: ## Build Docker image
	docker build -t $(FULL_IMAGE) .
	@echo "Image built: $(FULL_IMAGE)"

.PHONY: push
push: ## Push Docker image to registry
	docker push $(FULL_IMAGE)
	@echo "Image pushed: $(FULL_IMAGE)"

.PHONY: build-push
build-push: build push ## Build and push Docker image

.PHONY: install-crd
install-crd: ## Install the CustomResourceDefinition
	kubectl apply -f crd.yaml
	@echo "CRD installed successfully"

.PHONY: install-rbac
install-rbac: ## Install RBAC resources
	kubectl apply -f rbac.yaml
	@echo "RBAC resources installed"

.PHONY: deploy
deploy: ## Deploy the operator to Kubernetes
	kubectl apply -f deploy.yaml -n $(NAMESPACE)
	@echo "Operator deployed to namespace: $(NAMESPACE)"

.PHONY: install
install: install-crd install-rbac deploy ## Install CRD, RBAC, and deploy operator

.PHONY: uninstall
uninstall: ## Remove all operator resources
	kubectl delete -f deploy.yaml -n $(NAMESPACE) --ignore-not-found
	kubectl delete -f rbac.yaml --ignore-not-found
	kubectl delete -f crd.yaml --ignore-not-found
	@echo "Operator uninstalled"

.PHONY: logs
logs: ## View operator logs
	kubectl logs -l app=webpage-operator -n $(NAMESPACE) -f

.PHONY: status
status: ## Check operator status
	@echo "Operator Pod Status:"
	@kubectl get pods -l app=webpage-operator -n $(NAMESPACE)
	@echo "\nWebPage Resources:"
	@kubectl get webpages -A

.PHONY: example
example: ## Create example WebPage resource
	kubectl apply -f example-webpage.yaml
	@echo "Example WebPage created"

.PHONY: delete-example
delete-example: ## Delete example WebPage
	kubectl delete -f example-webpage.yaml --ignore-not-found
	@echo "Example WebPage deleted"

.PHONY: run-local
run-local: ## Run operator locally (requires kubeconfig)
	kopf run --verbose operator.py

.PHONY: test
test: ## Run basic tests
	@echo "Checking CRD installation..."
	@kubectl get crd webpages.devops.example.com
	@echo "Checking operator deployment..."
	@kubectl get deployment webpage-operator -n $(NAMESPACE)
	@echo "Checking RBAC..."
	@kubectl get clusterrole webpage-operator-role

.PHONY: clean
clean: ## Clean up local Python artifacts
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type f -name "*.pyc" -delete
	find . -type f -name "*.pyo" -delete
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true

.PHONY: format
format: ## Format Python code with black
	black operator.py

.PHONY: lint
lint: ## Lint Python code with flake8
	flake8 operator.py --max-line-length=100

.PHONY: all
all: build-push install example ## Build, push, install everything, and create example