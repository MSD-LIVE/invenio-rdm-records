# MSD-LIVE Overrides

MSD-LIVE is temporarily overriding the invenio-rdm-records package in order to add our own custom
metadata.  This is because RDM does not currently support hooking in custom metadata schema from your
application, so we have to add it here instead.

## How to pull changes from invenio

**Add the original invenio repository as a second remote:**
```bash
git remote add invenio https://github.com/inveniosoftware/invenio-rdm-records.git
```
**Pull the changes from the remote repo, v0.32.3 tag :**
```bash
git pull invenio v0.32.3 --allow-unrelated-histories
```
