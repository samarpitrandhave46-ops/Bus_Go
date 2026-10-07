# BusGo

BusGo is a Streamlit bus reservation application. SQLite is the default for local development; MySQL can be selected for deployment.

## Run locally

```powershell
python -m pip install -r requirements.txt
streamlit run app.py
```

The default SQLite database is created as `busgo.db`. Its demo accounts are `user@busgo.com` / `user123` and `admin@busgo.com` / `admin123`. Do not use these accounts in a deployed environment.

Set `BUSGO_SQLITE_PATH` to store the development database elsewhere when needed. MySQL deployments do not use this setting.

## Deploy to Streamlit Community Cloud

1. Push this project folder to a private GitHub repository.
2. In Streamlit Community Cloud, create an app from that repository and set the main file path to `app.py` (or `busgo/app.py` if the repository root is the parent folder).
3. Deploy once to obtain the app URL, then open the app's **Settings → Secrets** and add the TOML below, replacing each placeholder with values from your database and Razorpay dashboard.
4. Save the secrets and reboot the app. Never commit secrets or a local `busgo.db` file.

```toml
BUSGO_DB_ENGINE = "mysql"
MYSQL_HOST = "your-mysql-host"
MYSQL_PORT = "3306"
MYSQL_DATABASE = "busgo"
MYSQL_USER = "busgo_app"
MYSQL_PASSWORD = "your-database-password"
BUSGO_ENV = "production"
BUSGO_PUBLIC_URL = "https://your-app-name.streamlit.app"
BUSGO_ADMIN_EMAIL = "admin@example.com"
BUSGO_ADMIN_PASSWORD = "a-long-unique-password"
BUSGO_ADMIN_NAME = "BusGo Administrator"
RAZORPAY_KEY_ID = "your-razorpay-key-id"
RAZORPAY_KEY_SECRET = "your-razorpay-key-secret"
```

The MySQL server must accept connections from the deployed app. Restrict access to the narrowest network range supported by your database host; do not expose MySQL to the entire internet. Confirm the existing `users` schema and password format before deploying because BusGo does not migrate existing tables. Add real route and bus inventory in the Admin panel after the first admin login. Use Razorpay test credentials first, then switch to approved live credentials. Streamlit Community Cloud may sleep inactive apps, so MySQL (not the app filesystem) must remain the durable store. In production, `BUSGO_ENV=production` requires MySQL; the app will not silently fall back to seeded SQLite.

## Use MySQL

Set these environment variables in the deployment platform before starting Streamlit. Do not commit credentials to the repository.

```text
BUSGO_DB_ENGINE=mysql
MYSQL_HOST=your-mysql-host
MYSQL_PORT=3306
MYSQL_DATABASE=busgo
MYSQL_USER=busgo_app
MYSQL_PASSWORD=use-a-secret-from-your-host
BUSGO_ADMIN_EMAIL=admin@example.com
BUSGO_ADMIN_PASSWORD=use-a-long-unique-secret
BUSGO_ADMIN_NAME=BusGo Administrator
BUSGO_ENV=production
BUSGO_PUBLIC_URL=https://your-public-domain.example
RAZORPAY_KEY_ID=your-razorpay-key-id
RAZORPAY_KEY_SECRET=store-this-in-your-host-secret-manager
```

`MYSQL_DATABASE` may be set to the database that contains your existing `users` table. The app creates its `users`, `buses`, `routes`, and `bookings` tables when they are missing, but it does not migrate existing tables. An existing `users` table must provide `id`, `name`, `email`, `phone`, `password`, and `role`, with passwords stored in BusGo's `salt$hash` format. Back up the database and verify its schema before connecting. The attached `SELECT * FROM busgo.users` query does not show the columns or password format; inspect it with `SHOW CREATE TABLE busgo.users;` and check that existing user passwords are compatible.

The MySQL account needs permission to create tables on first startup and to select, insert, update, and delete app data. The administrator account is created only when the configured email is not already present; the bootstrap password is not used to update an existing account. Add routes and buses using the Admin panel after first login. MySQL mode does not seed demo users, routes, or buses. To populate an empty non-production MySQL database with sample inventory, set `BUSGO_SEED_DEMO_DATA=1`; this is ignored in production and never overwrites existing routes.

## Payments

BusGo creates a Razorpay Payment Link for each checkout. Customers enter card or UPI details on Razorpay's hosted page; BusGo does not collect card numbers or CVV. Seats are held while checkout is open and confirmed only after BusGo verifies the signed Razorpay callback and fetches a captured payment matching the booking amount. The callback URL is `BUSGO_PUBLIC_URL` and must be reachable by the customer's browser; production requires HTTPS. Use Razorpay test keys and test cards before launch. Razorpay does not support UPI Payment Links in test mode, so test UPI with live credentials only after the merchant account is approved and enabled.

When Razorpay keys are absent, local SQLite development shows a temporary simulated payment form for UI previews. It creates a clearly marked demo booking/receipt, charges nothing, and is disabled in production and MySQL mode. Never enter real payment details in the demo form.

The current settlement path depends on the customer returning through Razorpay's browser callback; there is no webhook/reconciliation worker yet. A paid callback that arrives after its seat has been resold enters `Payment Review` and does not issue a ticket. Configure a signed Razorpay webhook and support/refund handling before live bookings. Cancellation marks paid bookings as `Refund required`; it does not initiate a refund. Self-service password recovery is disabled until an identity-verified recovery channel is configured. Google sign-in, email/SMS notifications, and saved passenger profiles are not integrated. Use HTTPS, keep credentials in the hosting platform's secret store, restrict database network access, and configure backups and monitoring before launch.
