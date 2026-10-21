# Sumo Logic Terraform Import Generator

## Local Docker development environment

This project runs Python, PyStackQL, and Terraform inside Docker. It does not require Python, StackQL, or Terraform to be installed on macOS. Docker Desktop is required.

The image pins Python 3.12, PyStackQL 3.8.1, and Terraform 1.16.5. PyStackQL's bundled StackQL installer currently requires Linux amd64, so Compose uses amd64 emulation on Apple Silicon Macs. The project directory is mounted at `/workspace`, so source files and generated artifacts remain on the Mac. Terraform's provider plugin cache is kept in a named Docker volume.

There is a single Compose service, `web`. It serves a read-only explorer at http://localhost:8000. The app lists Sumo resources in a left nav and shows StackQL `SELECT` results as JSON. It never issues write queries or runs `terraform apply`.

```sh
docker compose up --build
```

Build the image and check the tools:

```sh
docker compose build
docker compose run --rm --no-deps web python -c 'from pystackql import StackQL; print("PyStackQL", StackQL().version)'
docker compose run --rm --no-deps web terraform version
```

Open a shell in the environment:

```sh
docker compose run --rm --no-deps --entrypoint bash web
```

Run the saved OpenTelemetry collector inventory query and write a timestamped JSON snapshot under the Git-ignored `inventory/` directory:

```sh
docker compose run --rm --no-deps web python scripts/list_ot_collectors.py
```

The snapshot includes the full selected collector fields, so treat it as potentially sensitive account data. Choose another path with `--output` if needed.

Fetch classic collectors with the StackQL query used by the running collector service:

```sh
docker compose run --rm --no-deps web python scripts/list_collectors.py --log-level DEBUG
```

Fetch one user with a timestamped snapshot and INFO logs:

```sh
docker compose run --rm --no-deps web python scripts/list_one_user.py
```

User snapshots can contain personal information and are written under the Git-ignored `inventory/` directory.

PyStackQL can be used directly from Python:

```python
from pystackql import StackQL

stackql = StackQL()  # Output defaults to Python dictionaries.
result = stackql.execute("SHOW SERVICES IN sumologic")
print(result)
```

The StackQL Sumo provider uses Sumo Logic Access ID and Access Key credentials with HTTP Basic auth. Copy `.env.example` to `.env`, fill in `SUMOLOGIC_ACCESSID`, `SUMOLOGIC_ACCESSKEY`, and your `SUMOLOGIC_ENVIRONMENT` deployment (for example, `us1`, `us2`, `eu`, or `in`). Compose passes those variables into the container at runtime, where both PyStackQL and the Sumo Terraform provider can read them. Use a read-only credential for inventory queries where possible. `.env` is excluded from Git and the Docker build context. No account credentials are needed for the image/tool smoke checks above.

Terraform commands run from the same mounted workspace. For example, after adding a provider configuration and initializing it:

```sh
docker compose run --rm --no-deps web terraform providers schema -json
```

The generated configuration, inventory, and state may contain sensitive account details. Keep local exports out of Git; `inventory/`, `generated/`, `.env`, state files, and plan files are ignored.

This environment is for development and plan review. It does not run `terraform apply` automatically.
