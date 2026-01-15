# Database Configuration Examples

## SQLite (Default)
```bash
# Use SQLite (default, no configuration needed)
DB_TYPE=sqlite
```

## MySQL
```bash
# Switch to MySQL
DB_TYPE=mysql

# MySQL connection details
MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=eamoon
MYSQL_PASSWORD=your_secure_password
MYSQL_DATABASE=eamoon
```

## Migration from SQLite to MySQL

### 1. Install MySQL dependencies
```bash
pip install -r requirements.txt
```

### 2. Create MySQL database
```sql
CREATE DATABASE eamoon CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'eamoon'@'localhost' IDENTIFIED BY 'your_secure_password';
GRANT ALL PRIVILEGES ON eamoon.* TO 'eamoon'@'localhost';
FLUSH PRIVILEGES;
```

### 3. Run migration script
```bash
# Using command-line arguments
python migrate_to_mysql.py \
  --sqlite data/inverter.db \
  --mysql-host localhost \
  --mysql-user eamoon \
  --mysql-password your_secure_password \
  --mysql-database eamoon

# Or using environment variables
export MYSQL_HOST=localhost
export MYSQL_USER=eamoon
export MYSQL_PASSWORD=your_secure_password
export MYSQL_DATABASE=eamoon
python migrate_to_mysql.py
```

### 4. Update environment and restart services
```bash
# Set DB_TYPE to mysql in your .env or systemd service
echo "DB_TYPE=mysql" >> .env

# Restart services
sudo systemctl restart easun-web easun-poller easun-bot
```

## Benefits of MySQL

- **Better concurrency**: No database locking issues with multiple services
- **Scalability**: Handle millions of readings efficiently
- **Native JSON**: Direct JSON column support (no string parsing)
- **Advanced queries**: Better indexing and query optimization
- **Replication**: Easy backup and replication setup
- **Remote access**: Access database from multiple servers

## Performance Comparison

| Feature | SQLite | MySQL |
|---------|--------|-------|
| Concurrent writes | Limited | Excellent |
| Max database size | 281 TB | Unlimited |
| JSON support | String | Native |
| Remote access | No | Yes |
| Replication | No | Yes |
| Setup complexity | None | Medium |

## Troubleshooting

### Database locked errors (SQLite)
If you see "database is locked" errors with SQLite, switch to MySQL for better concurrent access.

### Connection refused (MySQL)
Check MySQL is running: `sudo systemctl status mysql`

### Authentication failed
Verify credentials: `mysql -u eamoon -p -e "SELECT 1"`
