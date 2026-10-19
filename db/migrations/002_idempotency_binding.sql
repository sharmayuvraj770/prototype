-- Bind idempotency keys to the request that created them (load + canonical body hash).
alter table tms.api_idempotency add column load_id int;
alter table tms.api_idempotency add column request_hash text;
