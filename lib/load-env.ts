// For standalone processes (sandbox, worker, scripts). Next.js loads these files itself.
// Import this first: .env.local wins over .env, and real environment variables win over both.
import { config } from "dotenv";
config({ path: ".env.local", quiet: true });
config({ path: ".env", quiet: true });
