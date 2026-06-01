#!/usr/bin/env python3
"""
WebPage Kubernetes Operator
============================
This operator manages a custom resource called 'WebPage' that automatically
creates the necessary Kubernetes resources to serve static HTML content.

Reconciliation Loop Explained (for beginners):
----------------------------------------------
Kubernetes operators follow a "reconciliation loop" pattern:
1. User creates/updates a WebPage custom resource
2. Operator detects the change (via @kopf.on.create or @kopf.on.update)
3. Operator reads the desired state from the WebPage spec
4. Operator creates/updates ConfigMap, Deployment, and Service to match
5. Operator updates the WebPage status with the result
6. If anything goes wrong, Kopf will retry automatically

The goal is to make the actual cluster state match the desired state.
"""

import threading
import kopf
import kubernetes
import logging
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Dict, Any

# Configure logging for better debugging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Health check server
# ---------------------------------------------------------------------------
# deploy.yaml probes /healthz on port 8080 for liveness and readiness.
# Without this server running, Kubernetes will kill the operator pod in a
# loop thinking it has crashed.

class HealthHandler(BaseHTTPRequestHandler):
    """Minimal HTTP handler for Kubernetes liveness/readiness probes."""

    def do_GET(self):
        if self.path == '/healthz':
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b'ok')
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        # Suppress default access log noise from appearing in operator logs
        pass


def start_health_server(port: int = 8080):
    """Start the health check HTTP server in a background daemon thread."""
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f"Health check server listening on port {port}")
    server.serve_forever()


# Start health server before anything else so the pod passes its readiness
# probe while Kopf is still initialising.
threading.Thread(target=start_health_server, daemon=True).start()


# ---------------------------------------------------------------------------
# Kubernetes client — initialised once at module load time
# ---------------------------------------------------------------------------
# Calling load_incluster_config / load_kube_config on every event is wasteful
# and can mask connection errors. We do it once here and share the clients.

try:
    kubernetes.config.load_incluster_config()
    logger.info("Loaded in-cluster Kubernetes configuration")
except kubernetes.config.ConfigException:
    kubernetes.config.load_kube_config()
    logger.info("Loaded local kubeconfig configuration")

core_v1 = kubernetes.client.CoreV1Api()
apps_v1 = kubernetes.client.AppsV1Api()


# ---------------------------------------------------------------------------
# Kopf event handlers
# ---------------------------------------------------------------------------

@kopf.on.create('webpages.devops.example.com')
def create_webpage(spec: Dict[str, Any], name: str, namespace: str,
                   meta: Dict[str, Any], **kwargs) -> Dict[str, str]:
    """
    Handler for WebPage creation events.

    Triggered when a user applies a new WebPage resource.
    Orchestrates the creation of ConfigMap, Deployment, and Service.

    Args:
        spec: The 'spec' section from the WebPage YAML
        name: The name of the WebPage resource
        namespace: The namespace where the resource was created
        meta: Metadata from the WebPage resource

    Returns:
        Dict written into the WebPage's status subresource by Kopf
    """
    logger.info(f"Creating WebPage resource: {name} in namespace: {namespace}")

    html_content = spec.get('html', '<h1>Default Page</h1>')
    replicas = spec.get('replicas', 1)

    if not isinstance(replicas, int) or replicas < 0:
        raise kopf.PermanentError(
            f"Invalid replicas value: {replicas}. Must be a non-negative integer."
        )

    try:
        configmap = create_configmap(core_v1, name, namespace, html_content, meta)
        logger.info(f"ConfigMap '{configmap.metadata.name}' created successfully")

        deployment = create_deployment(apps_v1, name, namespace, replicas, meta)
        logger.info(f"Deployment '{deployment.metadata.name}' created with {replicas} replicas")

        service = create_service(core_v1, name, namespace, meta)
        logger.info(f"Service '{service.metadata.name}' created successfully")

        service_ip = service.spec.cluster_ip

        return {
            'status': 'Ready',
            'serviceIP': service_ip,
            'message': f'WebPage {name} is ready and serving at {service_ip}'
        }

    except kubernetes.client.exceptions.ApiException as e:
        logger.error(f"Kubernetes API error: {e}")
        raise kopf.TemporaryError(f"Failed to create resources: {e.reason}", delay=30)
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        raise kopf.PermanentError(f"Failed to create WebPage: {str(e)}")


@kopf.on.update('webpages.devops.example.com')
def update_webpage(spec: Dict[str, Any], name: str, namespace: str,
                   status: Dict[str, Any], **kwargs) -> Dict[str, str]:
    """
    Handler for WebPage update events.

    Triggered when a user modifies an existing WebPage resource.
    Updates the ConfigMap (HTML content) and Deployment (replica count).
    """
    logger.info(f"Updating WebPage resource: {name} in namespace: {namespace}")

    html_content = spec.get('html', '<h1>Default Page</h1>')
    replicas = spec.get('replicas', 1)

    # Same validation as create — must be checked on every write path
    if not isinstance(replicas, int) or replicas < 0:
        raise kopf.PermanentError(
            f"Invalid replicas value: {replicas}. Must be a non-negative integer."
        )

    try:
        update_configmap(core_v1, name, namespace, html_content)
        logger.info(f"ConfigMap '{name}-html' updated successfully")

        update_deployment(apps_v1, name, namespace, replicas)
        logger.info(f"Deployment '{name}-nginx' updated to {replicas} replicas")

        return {
            'status': 'Ready',
            'message': f'WebPage {name} updated successfully'
        }

    except kubernetes.client.exceptions.ApiException as e:
        logger.error(f"Kubernetes API error during update: {e}")
        raise kopf.TemporaryError(f"Failed to update resources: {e.reason}", delay=30)
    except Exception as e:
        logger.error(f"Unexpected error during update: {e}")
        raise kopf.PermanentError(f"Failed to update WebPage: {str(e)}")


@kopf.on.resume('webpages.devops.example.com')
def resume_webpage(spec: Dict[str, Any], name: str, namespace: str,
                   status: Dict[str, Any], **kwargs) -> Dict[str, str]:
    """
    Handler for operator restarts.

    When the operator pod restarts, Kopf fires 'on.resume' for every
    WebPage that already exists in the cluster. Without this handler,
    pre-existing WebPages would be silently ignored until someone manually
    edits them. This re-reconciles their actual state against the spec.
    """
    logger.info(f"Resuming reconciliation for WebPage: {name} in namespace: {namespace}")

    html_content = spec.get('html', '<h1>Default Page</h1>')
    replicas = spec.get('replicas', 1)

    if not isinstance(replicas, int) or replicas < 0:
        raise kopf.PermanentError(
            f"Invalid replicas value: {replicas}. Must be a non-negative integer."
        )

    try:
        update_configmap(core_v1, name, namespace, html_content)
        update_deployment(apps_v1, name, namespace, replicas)
        logger.info(f"WebPage {name} successfully re-reconciled after operator restart")

        return {
            'status': 'Ready',
            'message': f'WebPage {name} reconciled after operator restart'
        }

    except kubernetes.client.exceptions.ApiException as e:
        raise kopf.TemporaryError(f"Failed to reconcile on resume: {e.reason}", delay=30)
    except Exception as e:
        raise kopf.PermanentError(f"Failed to resume WebPage: {str(e)}")


@kopf.on.delete('webpages.devops.example.com')
def delete_webpage(name: str, namespace: str, **kwargs):
    """
    Handler for WebPage deletion events.

    Kopf automatically garbage-collects child resources that have
    ownerReferences pointing at the deleted WebPage, so no manual
    deletion is needed here. We log it for observability.
    """
    logger.info(f"WebPage '{name}' deleted from namespace '{namespace}'")
    logger.info("Child resources (ConfigMap, Deployment, Service) will be garbage collected")


# ---------------------------------------------------------------------------
# Resource helpers
# ---------------------------------------------------------------------------

def create_configmap(core_v1: kubernetes.client.CoreV1Api, name: str,
                     namespace: str, html_content: str,
                     owner_meta: Dict[str, Any]) -> kubernetes.client.V1ConfigMap:
    """
    Creates a ConfigMap to store the HTML content.
    Mounted as a volume into the Nginx pods at /usr/share/nginx/html.
    """
    configmap = kubernetes.client.V1ConfigMap(
        api_version="v1",
        kind="ConfigMap",
        metadata=kubernetes.client.V1ObjectMeta(
            name=f"{name}-html",
            namespace=namespace,
            owner_references=[kubernetes.client.V1OwnerReference(
                api_version="devops.example.com/v1",
                kind="WebPage",
                name=name,
                uid=owner_meta['uid'],
                controller=True,
                block_owner_deletion=True
            )]
        ),
        data={"index.html": html_content}
    )
    return core_v1.create_namespaced_config_map(namespace=namespace, body=configmap)


def update_configmap(core_v1: kubernetes.client.CoreV1Api, name: str,
                     namespace: str, html_content: str):
    """Patches an existing ConfigMap with new HTML content."""
    configmap_name = f"{name}-html"
    configmap = core_v1.read_namespaced_config_map(name=configmap_name, namespace=namespace)
    configmap.data["index.html"] = html_content
    core_v1.patch_namespaced_config_map(name=configmap_name, namespace=namespace, body=configmap)


def create_deployment(apps_v1: kubernetes.client.AppsV1Api, name: str,
                      namespace: str, replicas: int,
                      owner_meta: Dict[str, Any]) -> kubernetes.client.V1Deployment:
    """
    Creates an Nginx Deployment that mounts the ConfigMap as a volume.
    Nginx serves static files from /usr/share/nginx/html by default.
    """
    deployment = kubernetes.client.V1Deployment(
        api_version="apps/v1",
        kind="Deployment",
        metadata=kubernetes.client.V1ObjectMeta(
            name=f"{name}-nginx",
            namespace=namespace,
            owner_references=[kubernetes.client.V1OwnerReference(
                api_version="devops.example.com/v1",
                kind="WebPage",
                name=name,
                uid=owner_meta['uid'],
                controller=True,
                block_owner_deletion=True
            )]
        ),
        spec=kubernetes.client.V1DeploymentSpec(
            replicas=replicas,
            selector=kubernetes.client.V1LabelSelector(
                match_labels={"app": name, "managed-by": "webpage-operator"}
            ),
            template=kubernetes.client.V1PodTemplateSpec(
                metadata=kubernetes.client.V1ObjectMeta(
                    labels={"app": name, "managed-by": "webpage-operator"}
                ),
                spec=kubernetes.client.V1PodSpec(
                    containers=[
                        kubernetes.client.V1Container(
                            name="nginx",
                            image="nginx:1.25-alpine",
                            ports=[kubernetes.client.V1ContainerPort(container_port=80)],
                            volume_mounts=[
                                kubernetes.client.V1VolumeMount(
                                    name="html-content",
                                    mount_path="/usr/share/nginx/html"
                                )
                            ],
                            resources=kubernetes.client.V1ResourceRequirements(
                                requests={"cpu": "100m", "memory": "128Mi"},
                                limits={"cpu": "200m", "memory": "256Mi"}
                            )
                        )
                    ],
                    volumes=[
                        kubernetes.client.V1Volume(
                            name="html-content",
                            config_map=kubernetes.client.V1ConfigMapVolumeSource(
                                name=f"{name}-html"
                            )
                        )
                    ]
                )
            )
        )
    )
    return apps_v1.create_namespaced_deployment(namespace=namespace, body=deployment)


def update_deployment(apps_v1: kubernetes.client.AppsV1Api, name: str,
                      namespace: str, replicas: int):
    """Patches an existing Deployment with a new replica count."""
    deployment_name = f"{name}-nginx"
    deployment = apps_v1.read_namespaced_deployment(name=deployment_name, namespace=namespace)
    deployment.spec.replicas = replicas
    apps_v1.patch_namespaced_deployment(name=deployment_name, namespace=namespace, body=deployment)


def create_service(core_v1: kubernetes.client.CoreV1Api, name: str,
                   namespace: str, owner_meta: Dict[str, Any]) -> kubernetes.client.V1Service:
    """
    Creates a ClusterIP Service that routes traffic to the Nginx pods.
    Provides a stable internal IP for accessing the web page inside the cluster.
    """
    service = kubernetes.client.V1Service(
        api_version="v1",
        kind="Service",
        metadata=kubernetes.client.V1ObjectMeta(
            name=f"{name}-service",
            namespace=namespace,
            owner_references=[kubernetes.client.V1OwnerReference(
                api_version="devops.example.com/v1",
                kind="WebPage",
                name=name,
                uid=owner_meta['uid'],
                controller=True,
                block_owner_deletion=True
            )]
        ),
        spec=kubernetes.client.V1ServiceSpec(
            type="ClusterIP",
            selector={"app": name, "managed-by": "webpage-operator"},
            ports=[
                kubernetes.client.V1ServicePort(
                    protocol="TCP",
                    port=80,
                    target_port=80
                )
            ]
        )
    )
    return core_v1.create_namespaced_service(namespace=namespace, body=service)


if __name__ == "__main__":
    # Health server and k8s client are already initialised at module level above.
    # In production the operator is started via: kopf run operator.py
    logger.info("WebPage Operator starting...")
