-- match_vulnerability: nearest-neighbor lookup for src/dedup.py
--
-- Apply once after schema.sql against the Supabase Postgres DB. Required
-- because PostgREST (.table()/.rpc()) does not expose the pgvector `<=>`
-- operator directly; src/supabase_function.py:find_nearest_vulnerability
-- calls this function via supabase.rpc("match_vulnerability", ...).
--
-- Returns the single closest row by cosine distance, ignoring rows with
-- a NULL embedding. The HNSW index in schema.sql
-- (vulnerabilities_embedding_hnsw_idx) backs this query.

create or replace function public.match_vulnerability(query_embedding vector(384))
returns table(id uuid, subsector text, distance float)
language sql stable as $$
  select id, subsector, embedding <=> query_embedding as distance
  from public.vulnerabilities
  where embedding is not null
  order by embedding <=> query_embedding
  limit 1
$$;
