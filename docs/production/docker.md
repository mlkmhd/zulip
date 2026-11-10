# Running Zulip in Docker

Zulip provides an {doc}`official Docker image <docker:index>` for
deployments that prefer to run services in containers. We recommend
using it only if your organization has a preference for deploying
this way; the Docker image moderately increases the effort required
to install, maintain, and upgrade a Zulip installation, compared with
the [standard installer](install.md).

The Docker image supports two runtimes:

- **Docker Compose** for single-host deployments. See the {doc}`Docker
Compose getting started guide <docker:how-to/compose-getting-started>`.
- **Helm charts** for Kubernetes deployments. See the {doc}`Helm
getting started guide <docker:how-to/helm-getting-started>`.

Zulip's [backup tool](export-and-import.md#backups) supports
migrating between Docker and a standard installation, so you can
change your mind later.

For configuration, secrets, networking, upgrades, and other
operational topics, see the {doc}`Docker documentation
<docker:index>` in full.

## Publishing this fork's image

The `Publish Zulip Docker image` GitHub Actions workflow in `mlkmhd/zulip`
builds this fork with the production Dockerfile and startup scripts from a
pinned revision of [zulip/docker-zulip](https://github.com/zulip/docker-zulip).
It builds the exact source commit selected for the workflow, including the
fork's changes and compiled frontend assets. The source repository must be
public, because the Docker build clones it without credentials.

1. Commit and push your changes, including
   `.github/workflows/publish-docker-image.yml`, to the fork's default branch.
1. Enable GitHub Actions in the fork if it is disabled.
1. In **Actions**, select **Publish Zulip Docker image**, then **Run workflow**.
1. Select the branch to build and set **image_tag** to `12.5`, or another Docker
   tag. Leave **publish** enabled to upload the image; disable it to check the
   build without uploading anything.

The workflow uses GitHub's automatic `GITHUB_TOKEN` with `contents: read` and
`packages: write`; no personal access token or Docker Hub credentials are
needed. Repository policies must allow these permissions. If the GHCR package
already exists, grant this repository write access in the package's
**Manage Actions access** settings.

A successful publishing run uploads the `linux/amd64` image with two tags:

- `ghcr.io/mlkmhd/zulip:12.5` (or the selected image tag).
- `ghcr.io/mlkmhd/zulip:sha-<full-source-commit>` for tracing the source revision.

Running the workflow again with the same image tag replaces that tag. The
image tag does not change the application version declared in `version.py`.
The workflow runs only when started manually; pushes and pull requests do not
publish images.

To permit anonymous pulls, change the GHCR package's visibility to public in
its package settings after the first upload. You can then pull the image with:

```bash
docker pull ghcr.io/mlkmhd/zulip:12.5
```

Use the Compose configuration from the pinned `zulip/docker-zulip` revision,
setting the `zulip` service's `image` to this image and removing its `build`
section if present. The PostgreSQL, Redis, RabbitMQ, and Memcached services,
persistent data volumes, and deployment settings are still required. Follow
the Docker documentation linked above for configuration and upgrades.

To update the production container integration, review and update the pinned
`zulip/docker-zulip` revision in the workflow, then run a build-only check
before publishing.
