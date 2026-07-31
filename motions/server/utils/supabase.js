import { serverSupabaseClient, serverSupabaseServiceRole } from '#supabase/server'

export async function useSupabase(event) {
  return await serverSupabaseClient(event)
}

export function useSupabaseAdmin(event) {
  return serverSupabaseServiceRole(event)
}
