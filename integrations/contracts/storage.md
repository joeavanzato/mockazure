# Storage client qualification

C1 uses the original pinned Blob and File Share libraries and Az.Storage module.
All connection strings name local HTTPS service endpoints explicitly, and only
the instance CA is trusted by each isolated client process. Container/share
create, properties, metadata, and list round trips are separate from the future
C2/C3 byte-content qualification. SMB and NFS transports remain excluded.
