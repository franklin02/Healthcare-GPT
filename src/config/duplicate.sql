-- duplicates table
--
-- Mirrors public.vulnerabilities so duplicate articles flagged by
-- src/dedup.py can be preserved without polluting the primary table or
-- contaminating nearest-neighbor lookups. Each row points back to the
-- vulnerability it duplicates via original_vulnerability_id.

create table public.duplicates (
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
  embedding vector(384),
  original_vulnerability_id uuid references public.vulnerabilities(id) on delete set null,

  constraint duplicates_subsector_chk check (
    subsector in (
      'drug_shortage','medical_device_shortage','cyber_attack','natural_disaster','other'
    )
  ),
  constraint duplicates_confidence_chk check (
    confidence_level is null or confidence_level in ('high','medium','low')
  ),
  constraint duplicates_date_order_chk check (
    start_date is null or end_date is null or end_date >= start_date
  ),
  constraint duplicates_title_nonempty_chk check (length(btrim(title)) > 0),
  constraint duplicates_direct_link_nonempty_chk check (length(btrim(direct_link)) > 0),
  constraint duplicates_source_name_nonempty_chk check (length(btrim(source_name)) > 0)
);
alter table public.duplicates enable row level security;

create index if not exists duplicates_embedding_hnsw_idx
  on public.duplicates
  using hnsw (embedding vector_cosine_ops);