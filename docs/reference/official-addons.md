# Official Addons

Language: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/official-addons.md)

Addons extend a Ghost-ALICE OS install with optional capability packages on top of the core skills. This page is the contributor and operator reference for the addon sources surfaced on the homepage: official aliases, custom repositories, tenant packages, and local development addons. They all enter through the same installer entrypoint.

For the full install command surface, read [installation](../getting-started/installation.md). For how to package an addon you build or bring in, read the [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring).

## Contents

- [Official Addons](#official-addons-1)
- [Custom, Tenant, And Local Development Addons](#custom-tenant-and-local-development-addons)
- [Where To Go Next](#where-to-go-next)

## Official Addons

Official addons are maintained alongside the Ghost-ALICE core and are installed from the core checkout with a short alias. You do not need to know the repository URL; the alias resolves to the maintained source.

Install an official addon by passing its alias to the installer:

```bash
bash install.sh --addon autopilot
```

| Addon | Purpose | Install | Details |
| --- | --- | --- | --- |
| autopilot | Continue verified work items through the privileged autonomous adapter after explicit approval. | `bash install.sh --addon autopilot` | [AidALL/ghost-alice-autopilot](https://github.com/AidALL/ghost-alice-autopilot) |

## Custom, Tenant, And Local Development Addons

When an addon is not an official alias, install it from an explicit path or URL with `--addon-source`. The source can be a local directory, a fork, a tenant-specific package, or a checkout you are developing.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source` also accepts a repository URL:

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- Custom repositories: install an addon you maintain or fork by pointing `--addon-source` at its repository URL. The installer reads the addon manifest from that source instead of an official alias.
- Tenant packages: distribute an organization-specific or customer-specific addon bundle by pointing `--addon-source` at the tenant package location, so each tenant installs only the addons it is entitled to.
- Local development addons: while authoring an addon, point `--addon-source` at the working checkout on disk (for example `/path/to/addon-repo`) to install and re-test it without publishing first.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, official addons, custom addon sources, status, doctor, update, and platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/addon-authoring): manifest format and packaging guidance for addons you build or bring in.
