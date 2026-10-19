-- Tier-1 quote drafts (reversible; no DELETE route, only withdraw).
create table tms.quotes (id text primary key,               -- 'Q-' || 6 upper hex, server-generated
  tenant_id text not null references tms.tenants, created_by text not null references tms.users,
  customer_name text not null, orig_metro text not null references tms.metros,
  dest_metro text not null references tms.metros, equipment text not null check (equipment in ('reefer','dry_van')),
  pickup_date date not null, rate_per_mile numeric(6,2) not null check (rate_per_mile > 0 and rate_per_mile <= 20),
  miles int not null, total numeric(10,2) not null, notes text check (length(notes) <= 1000),
  status text not null check (status in ('draft','withdrawn')), version int not null default 1,
  created_at timestamptz not null, updated_at timestamptz not null);
create index on tms.quotes (tenant_id, status);
