# Sumo explorer

Browse a live Sumo Logic account and turn selected resources into Terraform.

The app reads the account with StackQL (list + GET), shows every service and resource in a left nav, and writes HCL from the saved snapshot plus the official Sumo Terraform provider schema. It never runs `terraform apply`. Use a read-only access key when you can.

## Get started

**1. Install Docker Desktop** and start it.

**2. Copy credentials**

```sh
cp .env.example .env
```

Fill in `SUMOLOGIC_ACCESSID`, `SUMOLOGIC_ACCESSKEY`, and `SUMOLOGIC_ENVIRONMENT` (`us1`, `us2`, `eu`, `au`, `in`, or `jp`). `.env` is gitignored.

**3. Start the explorer**

```sh
docker compose up --build
```

On Apple Silicon this runs Linux amd64 through Docker’s emulator (required by StackQL).

**4. Open** [http://localhost:8000](http://localhost:8000)

On first start the app loads `inventory/explorer_snapshot.json` if that file exists. If it does not, it walks every StackQL resource against Sumo. That can take a long time. Progress is in `docker compose logs -f web`. After a full pass, the snapshot is reused on the next start.

**5. Explore**

- Expand a service, then a resource, then click an object to see its GET JSON.
- Search needs at least three characters.
- **Refresh from Sumo** re-fetches the whole account and overwrites the snapshot.

**6. Generate Terraform**

- Check a resource type (all objects) or individual objects.
- Generate also includes upstream and downstream IDs found in that JSON (for example folders a dashboard lives in).
- Click **Generate Terraform**. Files go to `generated/tf/` (gitignored), with `imports.tf` for brownfield import.

This repo does not apply Terraform. Review the files, import, then manage them yourself.
