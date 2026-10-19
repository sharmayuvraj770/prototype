create schema if not exists tms;
create table tms.tenants (id text primary key, name text not null);
create table tms.users (id text primary key, tenant_id text not null references tms.tenants,
  email text not null unique, name text not null,
  role text not null check (role in ('ops_rep','carrier_sales','pricing','ops_manager','broker_admin')));
create table tms.metros (code text primary key, city text not null, state text not null,
  lat double precision not null, lon double precision not null, role text not null);
create table tms.lanes (lane_id text primary key, orig text not null references tms.metros,
  dest text not null references tms.metros, equipment text not null check (equipment in ('reefer','dry_van')),
  miles int not null, annual_loads_2024 int not null);
create table tms.rate_index (week date not null, market text not null, reefer_rpm numeric(6,2) not null,
  dry_van_rpm numeric(6,2) not null, availability text not null, n_obs int not null, primary key (week, market));
create table tms.rate_observations (id bigserial primary key, week date not null, origin_city text not null,
  destination text not null, rate_per_load numeric(10,2) not null, miles int not null,
  rate_per_mile numeric(6,2) not null, source text not null);
create table tms.waypoints (geoid text primary key, city text not null, state text not null,
  lat double precision not null, lon double precision not null);
create table tms.carriers (dot_number bigint primary key, mc_number text, legal_name text not null, dba_name text,
  city text, state text, lat double precision, lon double precision, home_metro text references tms.metros,
  equipment text not null check (equipment in ('reefer','dry_van')), power_units int, drivers int,
  safety_rating text not null, safety_rating_date date, recordable_crash_rate numeric,
  authority_since date, hazmat boolean not null default false, is_fictional boolean not null default false);
create table tms.carrier_compliance (dot_number bigint primary key references tms.carriers,
  authority_status text not null check (authority_status in ('active','inactive','revoked')),
  insurance_expires date not null, source_integration text not null, observed_at timestamptz not null);
create table tms.tenant_carriers (tenant_id text not null references tms.tenants, dot_number bigint not null references tms.carriers,
  on_time_pct numeric(5,2) not null, loads_hauled int not null, notes text, notes_updated_at timestamptz,
  primary key (tenant_id, dot_number));
create table tms.capacity_posts (id bigserial primary key, dot_number bigint not null references tms.carriers,
  equipment text not null, orig_metro text not null references tms.metros, dest_metro text references tms.metros,
  available_date date not null, source_integration text not null, posted_at timestamptz not null);
create table tms.loads (id int primary key, tenant_id text not null references tms.tenants, ref text not null,
  status text not null check (status in ('available','booked','dispatched','in_transit','delivered','cancelled')),
  lane_id text, orig_metro text not null references tms.metros, dest_metro text not null references tms.metros,
  equipment text not null, commodity text not null, weight_lbs int not null,
  pickup_at timestamptz not null, delivery_at timestamptz not null, eta timestamptz,
  carrier_dot bigint references tms.carriers, assigned_rep text not null references tms.users,
  customer_name text not null, customer_rate numeric(10,2) not null, carrier_rate numeric(10,2),
  updated_at timestamptz not null);
create index on tms.loads (tenant_id, status);
create index on tms.loads (tenant_id, assigned_rep);
create table tms.load_events (id bigserial primary key, load_id int not null references tms.loads,
  tenant_id text not null, type text not null check (type in
  ('created','booked','dispatched','picked_up','ping','delay','delivered','cancelled')),
  at timestamptz not null, city text, state text, lat double precision, lon double precision,
  source_integration text not null, detail text);
create index on tms.load_events (load_id, at desc);
create table tms.api_idempotency (tenant_id text not null, key text not null, response jsonb not null,
  created_at timestamptz not null default now(), primary key (tenant_id, key));
create table tms.seed_meta (id int primary key check (id = 1), seed_version text not null,
  demo_now timestamptz not null, seeded_at timestamptz not null);
