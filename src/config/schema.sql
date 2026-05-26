-- vulnerabilities table
create table public.vulnerabilities (
  id uuid primary key default gen_random_uuid(),
  source_name text not null,
  title text not null,
  direct_link text not null,
  subsector text not null,
  date_accessed timestamptz not null default now(),
  date_published text,
  content text,
  exec_summary text default '',
  confidence_level text, -- tbd by INL
  risk_level text, -- tbd by INL
  geography_scope text,
  start_date date,
  end_date date,
  resilience_or_mitigation_observed text,
  subsector_data jsonb       not null default '{}'::jsonb,

  constraint vulnerabilities_subsector_chk check (
    subsector in (
      'drug_shortage','medical_device_shortage','cyber_attack','natural_disaster','other'
    )
  ),
  constraint vulnerabilities_confidence_chk check (
    confidence_level is null or confidence_level in ('high','medium','low')
  ),
  constraint vulnerabilities_date_order_chk check (
    start_date is null or end_date is null or end_date >= start_date
  ),
  constraint vulnerabilities_title_nonempty_chk check (length(btrim(title)) > 0),
  constraint vulnerabilities_direct_link_nonempty_chk check (length(btrim(direct_link)) > 0),
  constraint vulnerabilities_source_name_nonempty_chk check (length(btrim(source_name)) > 0),
  constraint vulnerabilities_source_link_uniq unique (source_name, direct_link)
);
alter table public.vulnerabilities enable row level security;

-- noise table
create table public.noise (
  id uuid primary key default gen_random_uuid(),
  source_name text not null,
  title text not null,
  url text not null,
  reason text,
  body_preview text,
  date_accessed timestamptz not null default now(),

  constraint noise_url_nonempty_chk check (length(btrim(url)) > 0),
  constraint noise_source_name_nonempty_chk check (length(btrim(source_name)) > 0),
  constraint noise_source_url_uniq unique (source_name, url)
);
alter table public.noise enable row level security;
