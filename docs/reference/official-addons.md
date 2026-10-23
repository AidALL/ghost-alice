# Addons

언어: 🇺🇸 English | [🇰🇷 한국어](../ko/reference/official-addons.md)

Addons extend a Ghost-ALICE OS install with optional capability packages on top of the core skills. This page is the contributor and operator reference for addon sources: repositories, tenant packages, and local development checkouts. They all enter through the same installer entrypoint.

For the full install command surface, read [installation](../getting-started/installation.md). For how to package an addon you build or bring in, read the [addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/Addon-Authoring).

## Contents

- [Install An Addon](#install-an-addon)
- [Where To Go Next](#where-to-go-next)

## Install An Addon

Install an addon from an explicit path or URL with `--addon-source`. The source can be a local directory, a fork, a tenant-specific package, or a checkout you are developing.

```bash
bash install.sh --addon-source /path/to/addon-repo
```

`--addon-source` also accepts a repository URL:

```bash
bash install.sh --addon-source https://github.com/your-org/your-addon.git
```

- Custom repositories: install an addon you maintain or fork by pointing `--addon-source` at its repository URL. The installer reads the addon manifest from that source.
- Tenant packages: the tenant operator selects a bundle authorized for that organization or customer and passes its location through `--addon-source`. The installer reads that source’s addon manifest; choosing a source does not itself grant entitlement.
- Local development addons: while authoring an addon, point `--addon-source` at the working checkout on disk (for example `/path/to/addon-repo`) to install and re-test it without publishing first.

## Where To Go Next

- [Installation](../getting-started/installation.md): core install, addon sources, status, doctor, update, and platform selection.
- [Addon authoring guide](https://github.com/AidALL/ghost-alice/wiki/Addon-Authoring): manifest format and packaging guidance for addons you build or bring in.
