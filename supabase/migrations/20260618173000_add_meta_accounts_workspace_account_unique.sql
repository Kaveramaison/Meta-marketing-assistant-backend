alter table public.meta_accounts
add constraint meta_accounts_client_ad_account_key
unique (client_id, ad_account_id);
