import os
import io 
import base64 
import threading 
import time 
from datetime import datetime, timedelta, timezone 
import pandas as pd 
import plotly.graph_objects as go 
from sqlalchemy import create_engine, text 
import logging 
from logging.handlers import TimedRotatingFileHandler 
from dash import Dash, dcc, html, Input, Output, State, callback_context, no_update 
import dash_bootstrap_components as dbc

# --- CONFIG --- 
try: 
 from config import DB_CONFIG, TOTAL_CAPACITY, RESERVED_PERCENT 
except ImportError: 
 print("WARNING: config.py not found. Using defaults.") 
 DB_CONFIG = {"user": "postgres", "password": "password", "host": "localhost", "port": "5432", "dbname": "capacity_db"} 
 TOTAL_CAPACITY = {"CPU": 6336, "RAM": 50688, "STORAGE": 762.03} 
 RESERVED_PERCENT = 0.15 

# --- LOGGING --- 
logger = logging.getLogger("capacity_dashboard") 
logger.setLevel(logging.INFO) 
handler = TimedRotatingFileHandler("capacity.log", when="midnight", backupCount=7) 
handler.setFormatter(logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")) 
if not logger.handlers: logger.addHandler(handler) 

# --- SHAREPOINT SYNC (optional) --- 
try:
 from capacity_resource_collector import sync_events
except Exception as _sp_err:
 sync_events = None
 print(f"WARNING: SharePoint sync unavailable: {_sp_err}")

_sp_sync_lock = threading.Lock()
def sync_sharepoint_bg(start_date=None, end_date=None):
 """Run the SharePoint sync in a background thread (non-blocking)."""
 if not sync_events:
  return
 def _run():
  if not _sp_sync_lock.acquire(blocking=False):
   return  # a sync is already running
  try:
   n = sync_events(start_date, end_date)
   logger.info(f"SharePoint background sync: {n} item(s)")
  except Exception as e:
   logger.error(f"SharePoint sync failed: {e}")
  finally:
   _sp_sync_lock.release()
 threading.Thread(target=_run, daemon=True).start()

# No startup sync: the site shows the last-refreshed data until the user presses
# the "Refresh Data" button, which is the only trigger that pulls from SharePoint.

# --- DB ENGINE --- 
ENGINE = create_engine( 
 f"postgresql+psycopg2://{DB_CONFIG['user']}:{DB_CONFIG['password']}@" 
 f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}", 
 pool_pre_ping=True 
) 
CLOUD_ENGINE = create_engine( 
 f"postgresql+psycopg2://postgres:mc6Qld8x091U@10.7.32.181:5432/CloudInventory", 
 pool_pre_ping=True 
) 

# --- CONSTANTS --- 
INVENTORY_STATUS = ("Running", "Running/Not in Production") 
INVENTORY_LOCATION = "NJ Datacenter" 
INVENTORY_TYPE = "Server/VM" 
REFRESH_MS = 30 * 60 * 1000 # 30 Minutes 
RUN_RECONCILER = os.environ.get("RUN_RECONCILER", "true").lower() in ("true", "1", "yes") 
RECONCILE_INTERVAL = 300 
GLOBAL_THRESHOLDS = {"cpu_abs":1, "ram_mb_abs":512, "storage_gb_abs":5, "pct_tolerance":0.20} 
COLORS = {"background": "#f0f4f8", "cpu": "#2563eb", "ram": "#059669", "storage": "#7c3aed", "accent": "#3b82f6"} 

# --- AUTO-FIX DATABASE SCHEMA --- 
def fix_schema(): 
 try: 
  with ENGINE.begin() as conn: 
   conn.execute(text("ALTER TABLE capacity_events ADD COLUMN IF NOT EXISTS pending_inventory BOOLEAN DEFAULT TRUE")) 
   conn.execute(text("ALTER TABLE capacity_events ADD COLUMN IF NOT EXISTS reconciled BOOLEAN DEFAULT FALSE")) 
   conn.execute(text("ALTER TABLE capacity_events ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMP")) 
   conn.execute(text("ALTER TABLE capacity_events ADD COLUMN IF NOT EXISTS department TEXT")) 
   conn.execute(text("ALTER TABLE capacity_events ADD COLUMN IF NOT EXISTS user_id TEXT")) 
   # Create KPI history table 
   conn.execute(text(""" 
   CREATE TABLE IF NOT EXISTS kpi_daily_snapshot ( 
    id SERIAL PRIMARY KEY, 
    snapshot_date DATE NOT NULL UNIQUE, 
    cpu_used NUMERIC(15,2), 
    cpu_total NUMERIC(15,2), 
    cpu_reserved NUMERIC(15,2), 
    cpu_available NUMERIC(15,2), 
    cpu_pct NUMERIC(5,2), 
    ram_used NUMERIC(15,2), 
    ram_total NUMERIC(15,2), 
    ram_reserved NUMERIC(15,2), 
    ram_available NUMERIC(15,2), 
    ram_pct NUMERIC(5,2), 
    storage_used NUMERIC(15,2), 
    storage_total NUMERIC(15,2), 
    storage_reserved NUMERIC(15,2), 
    storage_available NUMERIC(15,2), 
    storage_pct NUMERIC(5,2), 
    created_at TIMESTAMP DEFAULT NOW() 
   ) 
   """)) 
  
  # Fix CloudInventory schema 
  with CLOUD_ENGINE.begin() as conn: 
   conn.execute(text(""" 
   CREATE TABLE IF NOT EXISTS vm_inventory ( 
    id SERIAL PRIMARY KEY, 
    resource_group VARCHAR(255) NOT NULL, 
    name VARCHAR(255) NOT NULL, 
    status VARCHAR(100), 
    private_ip VARCHAR(500), 
    os_type VARCHAR(100), 
    size VARCHAR(100), 
    cpu_vcpus VARCHAR(100), 
    ram_gb VARCHAR(100), 
    os_disk VARCHAR(100), 
    total_data_disk VARCHAR(100), 
    tags JSONB, 
    created_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    updated_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP, 
    inventory_date DATE NOT NULL, 
    UNIQUE(resource_group, name) 
   ) 
   """)) 
 except: pass 
fix_schema() 

# --- HELPERS --- 
def now_utc(): return datetime.now(timezone.utc) 
# Re-added these specific formatters to fix the NameError crash 
def fmt_cpu(v): return f"{int(round(float(v or 0))):,}" 
def fmt_ram_gb(v): return f"{float(v or 0):,.1f}" 
def fmt_storage_tb(v): return f"{(float(v or 0)/1024.0):,.2f}" 
def fmt_val_unit(v, type_): 
 if type_ == "CPU": return fmt_cpu(v), "vCPU" 
 elif type_ == "RAM": return fmt_ram_gb(v), "GB" 
 else: return fmt_storage_tb(v), "TB" 
def parse_csv(contents, filename): 
 if not contents: raise ValueError("No file") 
 header, content_string = contents.split(",", 1) 
 decoded = base64.b64decode(content_string) 
 df = pd.read_csv(io.StringIO(decoded.decode("utf-8"))) 
 lc = [c.lower() for c in df.columns] 
 if not {"date", "server", "cpu", "ram, storage".split(", ")[-1]}.issubset(set(lc)): 
  # Fix header check to exact names
  if not {"date", "server", "cpu", "ram", "storage"}.issubset(set(lc)): 
   raise ValueError("Headers must be: date, server, cpu, ram, storage") 
 mapping = {orig: orig.lower() for orig in df.columns} 
 df = df.rename(columns=mapping) 
 df["date"] = pd.to_datetime(df["date"], errors="coerce").dropna() 
 df["server"] = df["server"].astype(str) 
 for c in ["cpu", "ram", "storage"]: df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0) 
 return df.rename(columns={"ram": "ram_gb", "storage": "storage_gb"}) 
def detect_and_convert_ram_sum_to_gb(raw_sum, raw_max, baseline_gb): 
 try: rs = float(raw_sum); rm = float(raw_max) 
 except: return 0.0 
 if baseline_gb <= 0: return rs / 1024.0 if abs(rm) > 5000 else rs 
 if abs(rm) > abs(baseline_gb) * 10 or abs(rm) > 5000: return rs / 1024.0 
 return rs 
# --- KPI SNAPSHOT FUNCTIONS --- 
def store_kpi_snapshot(): 
 """Store daily KPI snapshot""" 
 try: 
  cap = calculate_capacity() 
  snap_date = datetime.now().date() 
  with ENGINE.begin() as conn: 
   conn.execute(text(""" 
   INSERT INTO kpi_daily_snapshot 
   (snapshot_date, cpu_used, cpu_total, cpu_reserved, cpu_available, cpu_pct, 
    ram_used, ram_total, ram_reserved, ram_available, ram_pct, 
    storage_used, storage_total, storage_reserved, storage_available, storage_pct) 
   VALUES (:sd, :cu, :ct, :cr, :ca, :cp, :ru, :rt, :rr, :ra, :rp, :su, :st, :sr, :sa, :sp) 
   ON CONFLICT (snapshot_date) DO UPDATE SET 
    cpu_used=EXCLUDED.cpu_used, cpu_total=EXCLUDED.cpu_total, cpu_reserved=EXCLUDED.cpu_reserved, 
    cpu_available=EXCLUDED.cpu_available, cpu_pct=EXCLUDED.cpu_pct, 
    ram_used=EXCLUDED.ram_used, ram_total=EXCLUDED.ram_total, ram_reserved=EXCLUDED.ram_reserved, 
    ram_available=EXCLUDED.ram_available, ram_pct=EXCLUDED.ram_pct, 
    storage_used=EXCLUDED.storage_used, storage_total=EXCLUDED.storage_total, 
    storage_reserved=EXCLUDED.storage_reserved, storage_available=EXCLUDED.storage_available, storage_pct=EXCLUDED.storage_pct 
   """), { 
    "sd": snap_date, 
    "cu": cap["CPU"]["used"], "ct": cap["CPU"]["total"], "cr": cap["CPU"]["reserved"], 
    "ca": cap["CPU"]["available"], "cp": cap["CPU"]["pct"], 
    "ru": cap["RAM"]["used"], "rt": cap["RAM"]["total"], "rr": cap["RAM"]["reserved"], 
    "ra": cap["RAM"]["available"], "rp": cap["RAM"]["pct"], 
    "su": cap["STORAGE"]["used"], "st": cap["STORAGE"]["total"], "sr": cap["STORAGE"]["reserved"], 
    "sa": cap["STORAGE"]["available"], "sp": cap["STORAGE"]["pct"] 
   }) 
  return True 
 except Exception as e: 
  logger.error(f"KPI snapshot error: {e}") 
  return False 
def get_kpi_history(days=90): 
 """Retrieve KPI history""" 
 try: 
  df = pd.read_sql(text(""" 
  SELECT snapshot_date, 
   cpu_used, cpu_total, cpu_reserved, cpu_available, cpu_pct, 
   ram_used, ram_total, ram_reserved, ram_available, ram_pct, 
   storage_used, storage_total, storage_reserved, storage_available, storage_pct 
  FROM kpi_daily_snapshot 
  WHERE snapshot_date >= NOW()::date - INTERVAL ':d days' 
  ORDER BY snapshot_date DESC 
  """), ENGINE, params={"d": days}) 
  return df 
 except: 
  return pd.DataFrame() 
def build_usage_trend_chart(df): 
 """Build line chart for CPU, RAM, Storage usage trends""" 
 if df.empty: 
  return go.Figure() 
 df = df.sort_values('snapshot_date') 
 df['snapshot_date'] = pd.to_datetime(df['snapshot_date']).dt.strftime('%Y-%m-%d') 
 fig = go.Figure() 
 fig.add_trace(go.Scatter(x=df['snapshot_date'], y=df['cpu_pct'], mode='lines+markers', 
  name='CPU %', line=dict(color=COLORS["cpu"], width=3))) 
 fig.add_trace(go.Scatter(x=df['snapshot_date'], y=df['ram_pct'], mode='lines+markers', 
  name='RAM %', line=dict(color=COLORS["ram"], width=3))) 
 fig.add_trace(go.Scatter(x=df['snapshot_date'], y=df['storage_pct'], mode='lines+markers', 
  name='Storage %', line=dict(color=COLORS["storage"], width=3))) 
 fig.update_layout(title="Usage Trend (% Used)", template="plotly_white", height=350, 
  xaxis_title="Date", yaxis_title="Usage %", 
  hovermode='x unified', margin=dict(l=40,r=10,t=40,b=30)) 
 return fig 
def build_resource_comparison_chart(df): 
 """Build bar chart comparing resources""" 
 if df.empty: 
  return go.Figure() 
 df = df.sort_values('snapshot_date') 
 df['snapshot_date'] = pd.to_datetime(df['snapshot_date']).dt.strftime('%Y-%m-%d') 
 fig = go.Figure() 
 fig.add_trace(go.Bar(x=df['snapshot_date'], y=df['cpu_used'], name='CPU Used (vCPU)', marker_color=COLORS["cpu"])) 
 fig.add_trace(go.Bar(x=df['snapshot_date'], y=df['ram_used'], name='RAM Used (GB)', marker_color=COLORS["ram"])) 
 fig.add_trace(go.Bar(x=df['snapshot_date'], y=df['storage_used'], name='Storage Used (GB)', marker_color=COLORS["storage"])) 
 fig.update_layout(title="Resource Usage Comparison", template="plotly_white", barmode='group', height=350, 
  xaxis_title="Date", yaxis_title="Usage (Mixed Units)", 
  margin=dict(l=40,r=10,t=40,b=30)) 
 return fig 
def build_utilization_heatmap(df): 
 """Build heatmap showing utilization over time""" 
 if df.empty: 
  return go.Figure() 
 df = df.sort_values('snapshot_date') 
 df['snapshot_date'] = pd.to_datetime(df['snapshot_date']).dt.strftime('%Y-%m-%d') 
 z_data = [df['cpu_pct'].tolist(), df['ram_pct'].tolist(), df['storage_pct'].tolist()] 
 resources = ['CPU %', 'RAM %', 'Storage %'] 
 fig = go.Figure(data=go.Heatmap(z=z_data, x=df['snapshot_date'], y=resources, colorscale='RdYlGn_r', 
  colorbar=dict(title="Usage %"))) 
 fig.update_layout(title="Utilization Heatmap", height=300, margin=dict(l=60,r=10,t=40,b=30)) 
 return fig 
def build_capacity_status_chart(df): 
 """Build gauge-style bar chart for current capacity""" 
 if df.empty: 
  return go.Figure() 
 latest = df.iloc[0] if not df.empty else None 
 if latest is None: 
  return go.Figure() 
 resources = ['CPU', 'RAM', 'Storage'] 
 percentages = [latest['cpu_pct'], latest['ram_pct'], latest['storage_pct']] 
 colors = [COLORS["cpu"], COLORS["ram"], COLORS["storage"]] 
 fig = go.Figure(data=[ 
  go.Bar(y=resources, x=percentages, orientation='h', marker=dict(color=colors), 
  text=[f"{p:.1f}%" for p in percentages], textposition='auto') 
 ]) 
 fig.update_layout(title="Current Capacity (%)", template="plotly_white", height=280, 
  xaxis_title="Usage %", xaxis=dict(range=[0, 100]), 
  margin=dict(l=100,r=10,t=40,b=30), showlegend=False) 
 return fig 
# --- CORE LOGIC --- 
def calculate_capacity(): 
 try: 
  inv = pd.read_sql(text(""" 
  SELECT COALESCE(SUM(NULLIF(TRIM(servercores), '')::numeric),0) AS cpu, 
         COALESCE(SUM(NULLIF(TRIM(servermemory), '')::numeric)/1024.0,0) AS ram_gb, 
         COALESCE(SUM(NULLIF(TRIM(totaldisk), '')::numeric),0) AS storage_gb 
  FROM inventory WHERE assetstatus IN (:s1, :s2) AND assetlocation = :loc AND assettype = :atype 
  """), ENGINE, params={"s1": INVENTORY_STATUS[0], "s2": INVENTORY_STATUS[1], "loc": INVENTORY_LOCATION, "atype": INVENTORY_TYPE}) 
  base_cpu = float(inv["cpu"].iloc[0] or 0) 
  base_ram = float(inv["ram_gb"].iloc[0] or 0) 
  base_stg = float(inv["storage_gb"].iloc[0] or 0) 
 except: base_cpu = base_ram = base_stg = 0.0 
 try: 
  ev = pd.read_sql(text(""" 
  SELECT COALESCE(SUM(cpu_delta) FILTER (WHERE reconciled = false), 0) AS cpu_sum, 
         COALESCE(SUM(ram_delta_gb) FILTER (WHERE reconciled = false), 0) AS ram_sum_raw, 
         COALESCE(MAX(ABS(ram_delta_gb)) FILTER (WHERE reconciled = false), 0) AS ram_max_raw, 
         COALESCE(SUM(storage_delta_gb) FILTER (WHERE reconciled = false), 0) AS storage_sum 
  FROM capacity_events 
  """), ENGINE) 
  cpu_sum = float(ev["cpu_sum"].iloc[0] or 0) 
  ram_sum = float(ev["ram_sum_raw"].iloc[0] or 0) 
  ram_max = float(ev["ram_max_raw"].iloc[0] or 0) 
  stg_sum = float(ev["storage_sum"].iloc[0] or 0) 
 except: cpu_sum = ram_sum = ram_max = stg_sum = 0.0 
 used_cpu = base_cpu + cpu_sum 
 used_ram = base_ram + detect_and_convert_ram_sum_to_gb(ram_sum, ram_max, base_ram) 
 used_stg = base_stg + stg_sum 
 total_stg_gb = TOTAL_CAPACITY.get("STORAGE", 0) * 1024.0 
 def mk_kpi(used, total): 
  reserved = total * RESERVED_PERCENT 
  usable = max(total - reserved, 0) 
  pct = (used / usable * 100) if usable > 0 else 0 
  return {"used": used, "total": total, "reserved": reserved, "available": max(usable - used, 0), "pct": pct} 
 return { 
  "CPU": mk_kpi(used_cpu, TOTAL_CAPACITY.get("CPU", 0)), 
  "RAM": mk_kpi(used_ram, TOTAL_CAPACITY.get("RAM", 0)), 
  "STORAGE": mk_kpi(used_stg, total_stg_gb) 
 } 
def build_gauge(title, value, max_val, color): 
 if max_val <= 0: max_val = 1 
 detail = f"{int(value):,} / {int(max_val):,}" 
 fig = go.Figure(go.Indicator( 
  mode = "gauge+number", 
  value = value, 
  domain = {'x': [0, 1], 'y': [0, 1]}, 
  title = {'text': f"{title}<br><span style='font-size:0.5em;color:#64748b'>{detail}</span>", 'font': {'size': 18, 'color': "#1e293b"}}, 
  gauge = { 
   'axis': {'range': [None, max_val], 'tickwidth': 1, 'tickcolor': "#495057"}, 
   'bar': {'color': color}, 
   'bgcolor': "white", 'borderwidth': 2, 'bordercolor': "#dee2e6", 
   'steps': [{'range': [0, max_val*0.75], 'color': '#f1f5f9'}, {'range': [max_val*0.75, max_val], 'color': '#fee2e2'}], 
   'threshold': {'line': {'color': "#ef4444", 'width': 4}, 'thickness': 0.75, 'value': max_val * 0.9} 
  } 
 )) 
 fig.update_layout(height=260, margin=dict(l=30,r=30,t=60,b=20), paper_bgcolor='rgba(0,0,0,0)', font={'color': "#1e293b"}) 
 return fig 
def build_top_consumers(resource): 
 col_map = {"CPU": "servercores", "RAM": "servermemory", "STORAGE": "totaldisk"} 
 col = col_map.get(resource) 
 try: 
  sql = f"SELECT assetuniquename, NULLIF(TRIM({col}), '')::numeric as val FROM inventory WHERE assetstatus IN (:s1, :s2) ORDER BY val DESC NULLS LAST LIMIT 5" 
  df = pd.read_sql(text(sql), ENGINE, params={"s1":INVENTORY_STATUS[0], "s2":INVENTORY_STATUS[1]}) 
  if df.empty: return go.Figure() 
  if resource == "RAM": 
   df["val"] = df["val"] / 1024.0 
   unit = "GB" 
  elif resource == "STORAGE": 
   unit = "GB" 
  else: 
   unit = "vCPU" 
  fig = go.Figure(go.Bar(x=df["val"], y=df["assetuniquename"], orientation='h', marker=dict(color=COLORS[resource.lower()], opacity=0.85))) 
  fig.update_layout(title=f"Top 5 {resource} Consumers", yaxis=dict(autorange="reversed"), xaxis_title=unit, template="plotly_white", height=250, margin=dict(l=10,r=10,t=40,b=20)) 
  return fig 
 except: return go.Figure() 
# --- RECONCILER --- 
_reconcile_lock = threading.Lock() 
def auto_reconcile_pending_events(): 
 matched = 0 
 try: 
  if not _reconcile_lock.acquire(blocking=False): return 
  df = pd.read_sql(text("SELECT id, assetuniquename, cpu_delta, ram_delta_gb, storage_delta_gb FROM capacity_events WHERE pending_inventory = true AND reconciled = false"), ENGINE) 
  for _, row in df.iterrows(): 
   inv = pd.read_sql(text("SELECT servercores, servermemory, totaldisk FROM inventory WHERE assetuniquename = :s LIMIT 1"), ENGINE, params={"s": row["assetuniquename"]}) 
   if inv.empty: continue 
   i_cpu = float(inv.iloc[0]["servercores"] or 0) 
   i_ram = float(inv.iloc[0]["servermemory"] or 0) 
   i_stg = float(inv.iloc[0]["totaldisk"] or 0) 
   e_cpu = float(row["cpu_delta"] or 0); e_ram = float(row["ram_delta_gb"] or 0); e_stg = float(row["storage_delta_gb"] or 0) 
   e_ram_mb = e_ram if abs(e_ram) > 1024 else e_ram * 1024.0 
   t = GLOBAL_THRESHOLDS 
   if ((abs(e_cpu - i_cpu) <= t["cpu_abs"] or abs(e_cpu - i_cpu) <= t["pct_tolerance"]*i_cpu) and 
       (abs(e_ram_mb - i_ram) <= t["ram_mb_abs"] or abs(e_ram_mb - i_ram) <= t["pct_tolerance"]*i_ram) and 
       (abs(e_stg - i_stg) <= t["storage_gb_abs"] or abs(e_stg - i_stg) <= t["pct_tolerance"]*i_stg)): 
    with ENGINE.begin() as conn: 
     conn.execute(text("UPDATE capacity_events SET reconciled=true, pending_inventory=false, reconciled_at=now() WHERE id=:id"), {"id": row["id"]}) 
    matched += 1 
  return matched 
 except: pass 
 finally: 
  if _reconcile_lock.locked(): _reconcile_lock.release() 
if RUN_RECONCILER: 
 def run_recon_loop(): 
  time.sleep(5) 
  while True: 
   auto_reconcile_pending_events() 
   time.sleep(RECONCILE_INTERVAL) 
 threading.Thread(target=run_recon_loop, daemon=True).start() 
# --- KPI SNAPSHOT THREAD --- 
def run_kpi_snapshot_loop(): 
 """Store KPI snapshots daily""" 
 time.sleep(10) # Wait 10 seconds after app start 
 while True: 
  try: 
   store_kpi_snapshot() 
   time.sleep(86400) # Run once per day (24 hours) 
  except Exception as e: 
   logger.error(f"KPI snapshot thread error: {e}") 
   time.sleep(3600) # Retry after 1 hour on error 
threading.Thread(target=run_kpi_snapshot_loop, daemon=True).start() 
def insert_event(server, cpu, ram_mb, storage_gb, source, date=None, pending=True): 
 try: 
  with ENGINE.begin() as conn: 
   conn.execute(text("INSERT INTO capacity_events (assetuniquename, cpu_delta, ram_delta_gb, storage_delta_gb, source, event_time, pending_inventory, reconciled) VALUES (:s, :c, :r, :st, :src, :et, :p, false)"), 
    {"s": server, "c": cpu, "r": ram_mb, "st": storage_gb, "src": source, "et": date or now_utc(), "p": pending}) 
  return True, "OK" 
 except Exception as e: return False, str(e) 
def delete_event(event_id): 
 try: 
  with ENGINE.begin() as conn: 
   res = conn.execute(text("DELETE FROM capacity_events WHERE id = :id"), {"id": event_id}) 
  return True if res.rowcount > 0 else False 
 except: return False 
# --- LAYOUT --- 
app = Dash(__name__, external_stylesheets=[dbc.themes.ZEPHYR, "https://cdn.jsdelivr.net/npm/bootstrap-icons@1.10.0/font/bootstrap-icons.css"], suppress_callback_exceptions=True) 
app.title = "Capacity Manager" 
login_layout = dbc.Card([ 
 dbc.CardBody([ 
  html.Div(html.I(className="bi bi-shield-lock-fill display-1" , style={"color": "#2563eb"}), className="text-center mb-4"), 
  html.H4("Restricted Access", className="text-center" , style={"color": "#1f2937", "marginBottom": "1.5rem"}), 
  dbc.Input(id="password-input", type="password", placeholder="Enter Password", className="mb-3", size="lg"), 
  dbc.Button("Unlock Settings", id="btn-login", color="primary", className="w-100", size="lg") 
 ]) 
], style={"maxWidth": "450px", "margin": "100px auto", "boxShadow": "0 10px 30px rgba(0,0,0,0.1)", "border": "1px solid #e5e7eb", "background": "#ffffff"}) 
app.index_string = ''' 
<!DOCTYPE html> 
<html> 
 <head>{%metas%}<title>{%title%}</title>{%favicon%}{%css%} 
 <style> 
 body { background: linear-gradient(135deg, #f0f4f8 0%, #e5e7eb 100%); font-family: 'Segoe UI', Tahoma, sans-serif; color: #1f2937; min-height: 100vh; } 
 .sidebar { position: fixed; top: 0; left: 0; bottom: 0; width: 17rem; padding: 2rem 1.5rem; background: linear-gradient(180deg, #1e40af 0%, #1f2937 100%); box-shadow: 4px 0 24px rgba(0,0,0,0.15); z-index: 1000; transition: all 0.3s ease; border-right: 2px solid #2563eb; } 
 .sidebar.collapsed { margin-left: -17rem !important; } 
 /* sidebar-toggle removed per user request */ 
 .content { margin-left: 18rem; padding: 2.5rem; transition: margin-left 0.3s ease; } 
 .content.expanded { margin-left: 2rem; } 
 .custom-card { border: none; border-radius: 16px; background: #ffffff; box-shadow: 0 4px 12px rgba(0,0,0,0.08); overflow: visible; transition: all 0.3s; border: 1px solid #e5e7eb; } 
 .custom-card:hover { transform: translateY(-4px); box-shadow: 0 12px 32px rgba(37, 99, 235, 0.1); border-color: #2563eb; } 
 .kpi-container { display: flex; align-items: center; justify-content: space-between; gap: 20px; } 
 .kpi-content { flex: 1; } 
 .kpi-icon-box { font-size: 3.5rem; opacity: 1; color: #e5e7eb; transition: all 0.2s; } 
 .kpi-title { font-size: 0.85rem; text-transform: uppercase; letter-spacing: 1.2px; color: #6b7280; font-weight: 700; margin-bottom: 8px; } 
 .kpi-value-row { display: flex; align-items: baseline; } 
 .kpi-value { font-size: 2.2rem; font-weight: 800; color: #1f2937; line-height: 1; } 
 .kpi-unit { font-size: 1.1rem; color: #9ca3af; font-weight: 600; margin-left: 8px; } 
 .kpi-detail { font-size: 0.85rem; color: #6b7280; display: flex; justify-content: space-between; margin-top: 12px; border-top: 1px solid #e5e7eb; padding-top: 8px; } 
 .nav-pills .nav-link { font-weight: 600; color: #d1d5db; margin-bottom: 8px; padding: 12px 16px; border-radius: 12px; transition: all 0.2s; } 
 .nav-pills .nav-link:hover { background: #1f2937; color: #60a5fa; transform: translateX(4px); } 
 .nav-pills .nav-link.active { background: #1e40af; color: #60a5fa; box-shadow: 0 2px 8px rgba(37, 99, 235, 0.2); border-left: 3px solid #60a5fa; padding-left: 13px; } 
 .table-modern thead th { border-top: none; border-bottom: 2px solid #e5e7eb; color: #1f2937; font-weight: 700; font-size: 0.85rem; text-transform: uppercase; padding: 12px; background: #f9fafb; } 
 .table-modern tbody td { vertical-align: middle; border-bottom: 1px solid #e5e7eb; font-size: 0.95rem; padding: 12px; color: #374151; } 
 .table-modern tbody tr:hover { background: #f3f4f6; } 
 .badge-manual { background-color: #dbeafe; color: #1e40af; padding: 6px 10px; border-radius: 6px; font-weight: 600; font-size: 0.75rem; } 
 .badge-csv { background-color: #d1fae5; color: #065f46; padding: 6px 10px; border-radius: 6px; font-weight: 600; font-size: 0.75rem; } 
 .badge-pending { color: #d97706; } 
 .badge-reconciled { color: #059669; } 
 .val-pos { color: #059669; font-weight: 700; background: rgba(5, 150, 105, 0.1); padding: 2px 6px; border-radius: 4px; } 
 .val-neg { color: #dc2626; font-weight: 700; background: rgba(220, 38, 38, 0.1); padding: 2px 6px; border-radius: 4px; } 
 .dashed-border { border: 2px dashed #d1d5db; background: #f9fafb; color: #6b7280; } 
 .dashed-border:hover { border-color: #2563eb; color: #2563eb; background: #eff6ff; } 
 .table-scroll-container { overflow-x: auto; max-width: 100%; border-radius: 8px; border: 1px solid #e5e7eb; } 
 .table-scroll-container .table { margin: 0; min-width: 100%; } 
 .table-scroll-container .table-modern thead th { white-space: nowrap; font-size: 0.8rem; padding: 10px 8px; } 
 .table-scroll-container .table-modern tbody td { white-space: nowrap; font-size: 0.85rem; padding: 10px 8px; overflow: hidden; text-overflow: ellipsis; max-width: 120px; } 
 .status-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; margin-right: 6px; } 
 .bg-success { background-color: #059669; } 
 .bg-secondary { background-color: #6b7280; } 

/* Allow calendar popup to escape card containers */
.custom-card,
.card,
.card-body {
    overflow: visible !important;
}
/* Keep calendar above sidebar, cards, graphs, tables */
.DayPicker_portal__horizontal,
.DateRangePicker_picker__portal,
.DateRangePicker_picker,
.DayPicker_transitionContainer,
.SingleDatePicker_picker {
    z-index: 2147483647 !important;
}
/* Ensure popup positioning works correctly */
.DateRangePicker,
.SingleDatePicker,
.DateInput {
    position: relative;
    overflow: visible !important;
}
/* Keep month navigation controls clickable above table content */
.DayPickerNavigation,
.DayPickerNavigation_button,
.CalendarMonth_caption {
  position: relative;
  z-index: 2147483647 !important;
  pointer-events: auto !important;
}
 </style> 
 </head> 
 <body>{%app_entry%}<footer>{%config%}{%scripts%}{%renderer%}</footer></body> 
</html> 
''' 
sidebar = html.Div([ 
 html.H3(["Capacity", html.Span("M", style={"color": "#60a5fa"})], className="display-6 fw-bold mb-5 px-2" , style={"color": "#ffffff"}), 
 dbc.Nav([ 
  dbc.NavLink([html.I(className="bi bi-speedometer2 me-3"), "Dashboard"], id="btn-dash", active=True), 
  dbc.NavLink([html.I(className="bi bi-list-check me-3"), "Events"], id="btn-ev"), 
  dbc.NavLink([html.I(className="bi bi-server me-3"), "Inventory"], id="btn-inv"), 
  dbc.NavLink([html.I(className="bi bi-bar-chart me-3"), "Stats"], id="btn-stats"), 
  dbc.NavLink([html.I(className="bi bi-gear me-3"), "Settings"], id="btn-set"), 
 ], vertical=True, pills=True, className="mb-4"), 
 html.Div([html.Small("Last Sync:", className="text-muted d-block small"), html.Small(id="last-sync", className="fw-bold d-none d-lg-block", style={"color": "#60a5fa"})], className="mt-auto pt-4 border-top") 
], className="sidebar d-flex flex-column") 
def kpi_card(title, used, unit, total, reserved, avail, pct, color, icon): 
 return dbc.Card([ 
  dbc.CardBody([ 
   html.Div([ 
    html.Div([ 
     html.Div(title, className="kpi-title"), 
     html.Div([ 
      html.Span(used, className="kpi-value"), 
      html.Span(unit, className="kpi-unit") 
     ], className="kpi-value-row"), 
     dbc.Progress(value=min(pct, 100), color=color, style={"height": "8px", "borderRadius": "4px"}, className="my-3"), 
     html.Div([html.Span(f"Total: {total}"), html.Span(f"Rsvd: {reserved}")], className="kpi-detail"), 
     html.Div([html.Span(f"Avail: {avail}", className="fw-bold"), html.Span(f"Used: {pct:.1f}%", className="fw-bold ms-3")], className="kpi-detail border-0 pt-1") 
    ], className="kpi-content"), 
    html.Div(html.I(className=f"{icon} kpi-icon-box"), className="kpi-icon-box") 
   ], className="kpi-container") 
  ]) 
 ], className="custom-card h-100") 
def mini_stat_card(title, value, color): 
 return dbc.Card(dbc.CardBody([ 
  html.H6(title, className="text-muted small text-uppercase fw-bold"), 
  html.H3(value, className=f"text-{color} mb-0 fw-bold") 
 ]), className="custom-card text-center h-100 border-0 shadow-sm") 
# --- VIEWS --- 
view_dash = html.Div(id="view-dash", children=[ 
 dcc.Loading(dbc.Row(id="kpi-row", className="g-4 mb-5")), 
 dbc.Row([ 
  dbc.Col([ 
   dbc.Row([ 
    dbc.Col(dbc.Card([dbc.CardHeader("CPU Load", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="cpu-gauge", config={'displayModeBar':False}))], className="custom-card mb-4"), width=12), 
    dbc.Col(dbc.Card([dbc.CardHeader("RAM Load", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="ram-gauge", config={'displayModeBar':False}))], className="custom-card mb-4"), width=12), 
    dbc.Col(dbc.Card([dbc.CardHeader("Storage Load", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="storage-gauge", config={'displayModeBar':False}))], className="custom-card"), width=12), 
   ]) 
  ], lg=4), 
  dbc.Col([ 
   dbc.Row([ 
    dbc.Col(dbc.Card([dbc.CardHeader("Top 5 CPU Consumers", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="cpu-top", config={'displayModeBar':False}))], className="custom-card mb-4"), width=12), 
    dbc.Col(dbc.Card([dbc.CardHeader("Top 5 RAM Consumers", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="ram-top", config={'displayModeBar':False}))], className="custom-card mb-4"), width=12), 
    dbc.Col(dbc.Card([dbc.CardHeader("Top 5 Storage Consumers", className="bg-white fw-bold border-0"), dbc.CardBody(dcc.Graph(id="storage-top", config={'displayModeBar':False}))], className="custom-card"), width=12), 
   ]) 
  ], lg=8), 
 ], className="g-4 mb-4") 
]) 
view_events = html.Div(id="view-ev", style={"display": "none"}, children=[ 
 dbc.Row(id="event-stats", className="mb-4 g-4"), 
 dbc.Row([ 
  dbc.Col(dbc.Card([ 
   dbc.CardHeader([html.I(className="bi bi-gear-wide-connected me-2"), "Event Actions"], className="bg-white border-0 fw-bold"), 
   dbc.CardBody([ 
    dbc.Tabs([ 
     dbc.Tab(label="Manual Allocation", children=[ 
      html.Div([ 
       dbc.Row([ 
        dbc.Col(dbc.Input(id="server", placeholder="Server Name (e.g. web-01)", className="mb-2"), md=3), 
        dbc.Col(dbc.Input(id="cpu", type="number", placeholder="CPU (+/-)"), md=2), 
        dbc.Col(dbc.Input(id="ram", type="number", placeholder="RAM GB (+/-)"), md=2), 
        dbc.Col(dbc.Input(id="stg", type="number", placeholder="Stg GB (+/-)"), md=2), 
        dbc.Col(dbc.Button("Submit Request", id="submit", color="primary", className="w-100"), md=3) 
       ], className="align-items-center mt-4 g-3"), 
       html.Div(id="man-msg", className="mt-3") 
      ]) 
     ]), 
     dbc.Tab(label="CSV Import", children=[ 
      dbc.Row([ 
       dbc.Col(dcc.Upload(id="csv-up", children=dbc.Button([html.I(className="bi bi-cloud-upload me-2"), "Upload CSV File"], color="light", className="w-100 dashed-border p-4")), md=6), 
       dbc.Col(html.Div(id="csv-msg"), md=6) 
      ], className="align-items-center mt-4") 
     ]), 
     dbc.Tab(label="Delete Event", children=[ 
      dbc.Row([ 
       dbc.Col(dbc.Input(id="del-id", placeholder="Event ID to Delete", type="number"), md=4), 
       dbc.Col(dbc.Button("Delete Event", id="btn-del", color="danger"), md=3), 
       dbc.Col(html.Div(id="del-msg", className="small mt-2"), md=5) 
      ], className="align-items-center mt-4") 
     ]) 
    ], className="nav-fill") 
   ]) 
  ], className="custom-card mb-5"), width=12) 
 ]), 
 dbc.Row([ 
  dbc.Col(dbc.Card([ 
   dbc.CardHeader([ 
    html.Span("Event Log History", className="fw-bold h5"), 
    html.Div([ 
     dbc.Button([html.I(className="bi bi-file-earmark-spreadsheet me-2"), "Export CSV"], id="btn-export-ev", size="sm", color="success", className="me-3"), 
     dcc.Download(id="download-event-csv"), 
    dcc.DatePickerRange(
     id="date-filter",
     start_date=(datetime.now()-timedelta(days=30)).date(),
     end_date=datetime.now().date(),
     display_format='YYYY-MM-DD',
     updatemode='bothdates',
     minimum_nights=0,
     number_of_months_shown=1,
     with_portal=False,
     style={'fontSize':'10px'}
    ) 
    ], className="float-end d-flex align-items-center") 
   ], className="bg-white border-0 d-flex justify-content-between align-items-center py-3"), 
   dbc.CardBody(dcc.Loading(html.Div(id="event-table", style={"maxHeight": "600px", "overflowY": "auto"}))) 
  ], className="custom-card"), width=12) 
 ]) 
]) 
view_inv = html.Div(id="view-inv", style={"display": "none"}, children=[ 
 dbc.Card([ 
  dbc.CardHeader([
   html.Span("Inventory", className="fw-bold h5"),
   dbc.Row([
    dbc.Col(dcc.Dropdown(
     id="inventory-type",
     options=[
      {"label": "Global Inventory", "value": "global"},
      {"label": "Cloud Inventory", "value": "cloud"}
     ],
     value="global",
     clearable=False,
     style={"width": "200px"}
    ), width="auto"),
    dbc.Col(dbc.Button([html.I(className="bi bi-download me-2"), "Export CSV"], id="btn-export", size="sm", color="success", className="float-end"), width="auto")
   ], className="d-flex justify-content-between align-items-center")
  ], className="bg-white border-0 py-3"), 
  dbc.CardBody([dcc.Download(id="download-csv"), html.Div(id="inv-table")]) 
 ], className="custom-card") 
]) 

# --- STATS VIEW (Modified controls row only) ---
view_stats = html.Div(id="view-stats", style={"display": "none"}, children=[
 dbc.Row([
  dbc.Col([
   dbc.Card([
    dbc.CardHeader("Filter & Controls", className="bg-white fw-bold border-0"),
    dbc.CardBody([
     # >>> Modified block starts
     dbc.Row(
      [
       dbc.Col(
        [
         html.H6("Date Range", className="fw-bold text-muted small mb-2"),
         dcc.DatePickerRange(
          id="stats-date-filter",
          start_date=(datetime.now()-timedelta(days=90)).date(),
          end_date=datetime.now().date(),
          display_format='YYYY-MM-DD',
          style={'fontSize':'10px'}
         ),
        ],
        md=8,
        className="d-flex flex-column justify-content-end",
       ),
       dbc.Col(
        [
         html.Div(
          [
           dbc.Button("Refresh", id="btn-refresh-stats", color="primary", size="sm"),
           dbc.Button([html.I(className="bi bi-download me-2"), "CSV"], id="btn-export-stats", size="sm", color="success", className="ms-2"),
           dcc.Download(id="download-stats-csv")
          ],
          className="d-flex justify-content-end align-items-end gap-2",
          style={"minHeight": "100%"},
         )
        ],
        md=4,
        className="d-flex",
       ),
      ],
      className="align-items-end",
     )
     # <<< Modified block ends
    ])
   ], className="custom-card")
  ], width=12, className="mb-4")
 ]),
 dbc.Row([
  dbc.Col(dbc.Card([
   dbc.CardHeader("Current Capacity Status", className="bg-white fw-bold border-0"),
   dbc.CardBody(dcc.Graph(id="stats-capacity-chart", config={'displayModeBar':False}))
  ], className="custom-card"), lg=4, className="mb-4"),
  dbc.Col(dbc.Card([
   dbc.CardHeader("Resource Comparison", className="bg-white fw-bold border-0"),
   dbc.CardBody(dcc.Graph(id="stats-comparison-chart", config={'displayModeBar':False}))
  ], className="custom-card"), lg=8, className="mb-4")
 ]),
 dbc.Row([
  dbc.Col(dbc.Card([
   dbc.CardHeader("Usage Trend (% Over Time)", className="bg-white fw-bold border-0"),
   dbc.CardBody(dcc.Graph(id="stats-trend-chart", config={'displayModeBar':False}))
  ], className="custom-card"), width=12, className="mb-4")
 ]),
 dbc.Row([
  dbc.Col(dbc.Card([
   dbc.CardHeader("Utilization Heatmap", className="bg-white fw-bold border-0"),
   dbc.CardBody(dcc.Graph(id="stats-heatmap", config={'displayModeBar':False}))
  ], className="custom-card"), width=12, className="mb-4")
 ]),
 dbc.Row([
  dbc.Col(dbc.Card([
   dbc.CardHeader([html.Span("Daily Snapshot Data", className="fw-bold h5")], className="bg-white border-0 py-3"),
   dbc.CardBody(html.Div(id="stats-table", style={"maxHeight": "500px", "overflowY": "auto"}))
  ], className="custom-card"), width=12)
 ])
])

# Settings View (Defaults to Login Layout) 
view_set = html.Div(id="view-set", style={"display": "none"}, children=[ 
 html.Div(id="settings-content", children=[login_layout]) 
]) 
app.layout = html.Div([ 
 sidebar, 
 html.Div([ 
  dbc.Row([dbc.Col(html.H4("Dashboard Overview", className="fw-bold", style={"color": "#1f2937"})), dbc.Col(dbc.Button([html.I(className="bi bi-arrow-clockwise me-2"), "Refresh Data"], id="refresh", color="primary", className="shadow-sm"), width="auto")], className="mb-5"), 
  dcc.Interval(id="timer", interval=REFRESH_MS), 
  view_dash, view_events, view_inv, view_stats, view_set 
 ], className="content", id="main-content") 
], style={"background": "#f0f4f8"}) 
# --- CALLBACKS --- 
@app.callback( 
 [Output("view-dash", "style"), Output("view-ev", "style"), Output("view-inv", "style"), Output("view-stats", "style"), Output("view-set", "style"), 
  Output("btn-dash", "active"), Output("btn-ev", "active"), Output("btn-inv", "active"), Output("btn-stats", "active"), Output("btn-set", "active")], 
 [Input("btn-dash", "n_clicks"), Input("btn-ev", "n_clicks"), Input("btn-inv", "n_clicks"), Input("btn-stats", "n_clicks"), Input("btn-set", "n_clicks")] 
) 
def nav(b1, b2, b3, b4, b5): 
 ctx = callback_context 
 bid = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else "btn-dash" 
 s, h = {"display": "block"}, {"display": "none"} 
 if bid == "btn-ev": return h, s, h, h, h, False, True, False, False, False 
 if bid == "btn-inv": return h, h, s, h, h, False, False, True, False, False 
 if bid == "btn-stats": return h, h, h, s, h, False, False, False, True, False 
 if bid == "btn-set": return h, h, h, h, s, False, False, False, False, True 
 return s, h, h, h, h, True, False, False, False, False 
@app.callback( 
 Output("settings-content", "children"), 
 [Input("btn-login", "n_clicks"), Input("btn-update-th", "n_clicks"), Input("btn-force-rec", "n_clicks"), Input("btn-reset-db", "n_clicks")], 
 [State("password-input", "value"), State("t-cpu", "value"), State("t-ram", "value"), State("t-pct", "value")], 
 prevent_initial_call=False 
) 
def security_check(n_log, n_upd, n_rec, n_rst, pwd, t_cpu, t_ram, t_pct): 
 ctx = callback_context 
 trig = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else "" 
 msg = "" 
 if trig == "btn-update-th": 
  GLOBAL_THRESHOLDS.update({"cpu_abs":t_cpu, "ram_mb_abs":t_ram, "pct_tolerance":t_pct}) 
  msg = "Thresholds Updated" 
 if trig == "btn-force-rec": 
  auto_reconcile_pending_events() 
  msg = "Reconciler Triggered" 
 if trig == "btn-reset-db": 
  try: 
   with ENGINE.begin() as conn: 
    conn.execute(text("TRUNCATE TABLE capacity_events RESTART IDENTITY;")) 
   msg = "DB Reset: IDs at 1" 
  except Exception as e: msg = f"Error: {e}" 
 settings_ui = html.Div([ 
  dbc.Row([ 
   dbc.Col(dbc.Card([ 
    dbc.CardHeader("Database Config", className="fw-bold border-0", style={"color": "#f1f5f9", "background": "#334155"}), 
    dbc.CardBody([ 
     dbc.Row([dbc.Col("Host:", width=4, className="fw-bold"), dbc.Col(DB_CONFIG['host'])]), 
     dbc.Row([dbc.Col("DB:", width=4, className="fw-bold"), dbc.Col(DB_CONFIG['dbname'])]), 
     dbc.Button("Test Reconcile Now", id="btn-force-rec", color="warning", size="sm", className="mt-3") 
    ]) 
   ], className="custom-card"), md=6), 
   dbc.Col(dbc.Card([ 
    dbc.CardHeader("Reconcile Thresholds", className="fw-bold border-0", style={"color": "#f1f5f9", "background": "#334155"}), 
    dbc.CardBody([ 
     dbc.Row([dbc.Col("CPU Abs:", width=6), dbc.Col(dbc.Input(id="t-cpu", type="number", value=GLOBAL_THRESHOLDS['cpu_abs'], size="sm"))], className="mb-2"), 
     dbc.Row([dbc.Col("RAM MB Abs:", width=6), dbc.Col(dbc.Input(id="t-ram", type="number", value=GLOBAL_THRESHOLDS['ram_mb_abs'], size="sm"))], className="mb-2"), 
     dbc.Row([dbc.Col("Tolerance %:", width=6), dbc.Col(dbc.Input(id="t-pct", type="number", value=GLOBAL_THRESHOLDS['pct_tolerance'], size="sm"))], className="mb-2"), 
     dbc.Button("Update Thresholds", id="btn-update-th", color="primary", size="sm", className="mt-2"), 
    ]) 
   ], className="custom-card"), md=6) 
  ]), 
  dbc.Row([ 
   dbc.Col(dbc.Card([ 
    dbc.CardHeader("Danger Zone", className="bg-danger text-white fw-bold border-0"), 
    dbc.CardBody([ 
     html.P("Clear event history and reset IDs to 1. Inventory is not affected.", className="small text-muted"), 
     dbc.Button("Reset & Clear Event Log", id="btn-reset-db", color="danger", outline=True, size="sm", className="w-100") 
    ]) 
   ], className="custom-card mt-4 border-danger"), md=12) 
  ]), 
  html.Div(msg, className="mt-3 text-center fw-bold text-success") if msg else None 
 ]) 
 if trig == "btn-login" and pwd and pwd == "sysadmin": 
  return settings_ui 
 elif trig in ["btn-update-th", "btn-force-rec", "btn-reset-db"]: 
  return settings_ui 
 return login_layout 
@app.callback( 
 [Output("kpi-row", "children"), 
  Output("cpu-gauge", "figure"), Output("ram-gauge", "figure"), Output("storage-gauge", "figure"), 
  Output("cpu-top", "figure"), Output("ram-top", "figure"), Output("storage-top", "figure"), 
  Output("event-table", "children"), Output("event-stats", "children"), Output("inv-table", "children"), 
  Output("man-msg", "children"), Output("csv-msg", "children"), Output("del-msg", "children"), 
  Output("download-csv", "data"), Output("download-event-csv", "data"), Output("last-sync", "children")], 
 [Input("timer", "n_intervals"), Input("refresh", "n_clicks"), Input("submit", "n_clicks"), Input("csv-up", "contents"), 
  Input("btn-del", "n_clicks"), Input("date-filter", "start_date"), Input("date-filter", "end_date"), 
  Input("btn-export", "n_clicks"), Input("btn-export-ev", "n_clicks"), Input("inventory-type", "value")], 
 [State("server", "value"), State("cpu", "value"), State("ram", "value"), State("stg", "value"), State("csv-up", "filename"), State("del-id", "value")] 
) 
def update_main(n_t, n_ref, n_sub, csv, n_del, d_start, d_end, n_exp, n_exp_ev, inventory_type, srv, cpu, ram, stg, fname, del_id): 
 ctx = callback_context; trig = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else "" 
 m_msg = c_msg = d_msg = dl_data = dl_ev_data = no_update 
 force = False 
 if trig == "submit" and n_sub: 
  if not srv: m_msg = dbc.Alert("Server required", color="danger") 
  else: 
   try: 
    with ENGINE.connect() as conn: ex = conn.execute(text("SELECT 1 FROM inventory WHERE assetuniquename=:s"), {"s":srv}).fetchone() 
    ok, txt = insert_event(srv, float(cpu or 0), float(ram or 0)*1024, float(stg or 0), "MANUAL", pending=(ex is None)) 
    m_msg = dbc.Toast("Saved", header="Success", icon="success", duration=3000, is_open=True, style={"position":"fixed","top":10,"right":10}) if ok else dbc.Alert(txt, color="danger") 
    force = True 
   except Exception as e: m_msg = dbc.Alert(str(e), color="danger") 
 if trig == "csv-up" and csv: 
  try: 
   df = parse_csv(csv, fname); cnt = 0 
   with ENGINE.connect() as conn: 
    for _, r in df.iterrows(): 
     ex = conn.execute(text("SELECT 1 FROM inventory WHERE assetuniquename=:s"), {"s":r["server"]}).fetchone() 
     ok, _ = insert_event(r["server"], r["cpu"], r["ram_gb"]*1024, r["storage_gb"], "CSV", r["date"], pending=(ex is None)) 
     if ok: cnt += 1 
   c_msg = dbc.Toast(f"Imported {cnt}", header="Success", icon="success", duration=3000, is_open=True, style={"position":"fixed","top":10,"right":10}); force = True 
  except Exception as e: c_msg = dbc.Alert(str(e), color="danger") 
 if trig == "btn-del" and n_del and del_id: 
  if delete_event(del_id): d_msg = dbc.Alert(f"Deleted ID {del_id}", color="success"); force = True 
  else: d_msg = dbc.Alert("ID not found", color="danger") 
 if force or trig in ("timer", "refresh", "date-filter", "btn-export", "btn-export-ev", "inventory-type", ""): 
  # Pull the latest SharePoint list into the Event Log ONLY on "Refresh Data", and
  # do it first, so every panel (KPIs, gauges, event table) reflects it right away. 
  if sync_events and trig == "refresh": 
   try: 
    sync_events(d_start, d_end) 
   except Exception as e: 
    logger.error(f"SharePoint sync failed: {e}") 
  cap = calculate_capacity() 
  cards = [] 
  for k, i, c in [("CPU","bi-cpu",COLORS["cpu"]),("RAM","bi-memory",COLORS["ram"]),("STORAGE","bi-hdd-network",COLORS["storage"])]: 
   d = cap[k] 
   v_fmt, u_fmt = fmt_val_unit(d["used"], k) 
   c_real = "danger" if d["pct"] > 90 else ("warning" if d["pct"] > 80 else ("info" if k=="STORAGE" else "primary")) 
   # Helper for local formatting inside loop 
   def f(val): 
    v, u = fmt_val_unit(val, k) 
    return f"{v} {u}" 
   cards.append(dbc.Col(kpi_card(k, v_fmt, u_fmt, f(d["total"]), f(d["reserved"]), f(d["available"]), d["pct"], c_real, i), md=4)) 
  ev_tbl = html.Div("No events", className="text-muted p-3") 
  estats_cols = [] 
  try: 
   stats = pd.read_sql(text("SELECT COUNT(*) as tot, COUNT(*) FILTER (WHERE pending_inventory) as pend FROM capacity_events WHERE event_time::date BETWEEN :s AND :e"), ENGINE, params={"s": d_start, "e": d_end}) 
   tot = stats.iloc[0]['tot']; pend = stats.iloc[0]['pend'] 
   estats_cols = [dbc.Col(mini_stat_card("Total Events", tot, "primary"), md=6), dbc.Col(mini_stat_card("Pending Reconcile", pend, "warning"), md=6)] 
   q = "SELECT id, event_time, source, assetuniquename, department, user_id, cpu_delta, ram_delta_gb, storage_delta_gb, reconciled FROM capacity_events WHERE event_time::date BETWEEN :s AND :e ORDER BY event_time DESC" 
   ev_df = pd.read_sql(text(q), ENGINE, params={"s": d_start, "e": d_end}) 
   if trig == "btn-export-ev" and not ev_df.empty: dl_ev_data = dcc.send_data_frame(ev_df.to_csv, "events_export.csv") 
   if not ev_df.empty: 
    def make_badge(src): return html.Span(src, className="badge-manual" if src=="MANUAL" else "badge-csv") 
    def make_status(r): return html.I(className="bi bi-check-circle-fill badge-reconciled") if r else html.I(className="bi bi-clock-history badge-pending") 
    def color_val(v): return html.Span(f"{v:+.1f}" if isinstance(v, float) else f"{v:+}", className="val-pos" if v>0 else ("val-neg" if v<0 else "")) 
    def txt_val(v): return "" if pd.isna(v) else str(v) 
    rows = [html.Tr([html.Td(r["id"]), html.Td(str(r["event_time"])[:16]), html.Td(make_badge(r["source"])), html.Td(r["assetuniquename"]), html.Td(txt_val(r["department"])), html.Td(txt_val(r["user_id"])), html.Td(color_val(r["cpu_delta"])), html.Td(color_val(r["ram_delta_gb"])), html.Td(color_val(r["storage_delta_gb"])), html.Td(make_status(r["reconciled"]), className="text-center")]) for _, r in ev_df.iterrows()] 
    header = [html.Th("ID"), html.Th("Time"), html.Th("Source"), html.Th("Server"), html.Th("Department"), html.Th("User ID"), html.Th("CPU (cores)"), html.Th("RAM (GB)"), html.Th("STG (GB)"), html.Th("Sts")] 
    ev_tbl = dbc.Table([html.Thead(html.Tr(header)), html.Tbody(rows)], striped=True, hover=True, className="table-modern") 
  except: pass 
  inv_tbl = html.P("No Data") 
  try: 
   if inventory_type == "cloud":
    # Cloud Inventory query
    cloud_sql = text("""
     SELECT 
      resource_group AS "Resource Group",
      name AS "Name",
      status AS "Status",
      private_ip AS "Private IP",
      os_type AS "OS",
      size AS "Size",
      cpu_vcpus AS "CPU vCPUs",
      ram_gb AS "RAM GB",
      os_disk AS "OS Disk",
      total_data_disk AS "Total Data Disk",
      tags->>'ApplicationName' AS "Application Name",
      tags->>'Domain' AS "Domain",
      tags->>'Environment' AS "Environment",
      tags->>'Owner' AS "Owner"
     FROM vm_inventory
     ORDER BY name
    """)
    inv_df = pd.read_sql(cloud_sql, CLOUD_ENGINE)
   else:
    # Global Inventory query (existing logic)
    base_sql = text( 
     "SELECT assetuniquename AS \"Server\", assetipaddress AS \"IP\", assetstatus AS \"Status\", servercores AS \"CPU\", " 
     "ROUND(COALESCE(NULLIF(TRIM(servermemory),'') ,'0')::numeric/1024.0, 2) AS \"RAM (GB)\", totaldisk AS \"Storage (GB)\" " 
     "FROM inventory WHERE assetstatus IN (:s1, :s2) AND assetlocation=:loc AND assettype=:at" 
    ) 
    inv_df = pd.read_sql(base_sql, ENGINE, params={"s1":INVENTORY_STATUS[0], "s2":INVENTORY_STATUS[1], "loc":INVENTORY_LOCATION, "at":INVENTORY_TYPE}) 
    # Try to find an OS-like column and merge it in if present 
    try: 
     cols_df = pd.read_sql(text("SELECT column_name FROM information_schema.columns WHERE table_name='inventory'"), ENGINE) 
     # Prefer the explicit 'serveros' column if present, then fall back to common names 
     candidates = ['serveros','assetos','asset_os','os','operatingsystem','operating_system','osname','assetoperatingsystem'] 
     found = None 
     if not cols_df.empty: 
      # use actual column_name values and compare lowercased 
      for col in cols_df['column_name'].tolist(): 
       if col.lower() in candidates: 
        found = col 
        break 
     if found and not inv_df.empty: 
      os_sql = text(f"SELECT assetuniquename AS server, {found} AS os FROM inventory WHERE assetstatus IN (:s1, :s2) AND assetlocation=:loc AND assettype=:at") 
      os_df = pd.read_sql(os_sql, ENGINE, params={"s1":INVENTORY_STATUS[0], "s2":INVENTORY_STATUS[1], "loc":INVENTORY_LOCATION, "at":INVENTORY_TYPE}) 
      if not os_df.empty: 
       inv_df = inv_df.merge(os_df, left_on='Server', right_on='server', how='left') 
       inv_df = inv_df.drop(columns=['server']) 
       inv_df = inv_df.rename(columns={'os': 'OS'}) 
      else: 
       inv_df['OS'] = '' 
     else: 
      inv_df['OS'] = '' 
    except Exception as e: 
     logger.exception('Error detecting OS column: %s', e) 
     inv_df['OS'] = '' 
   if trig == "btn-export" and not inv_df.empty: 
    dl_data = dcc.send_data_frame(inv_df.to_csv, "inventory_export.csv") 
   if not inv_df.empty: 
    if inventory_type == "global":
     inv_df["Status"] = inv_df["Status"].apply(lambda s: html.Span([html.Span(className=f"status-dot bg-{'success' if 'Running' in s else 'secondary'}"), s])) 
     header = [html.Th(c, title=c) for c in inv_df.columns] 
     rows = [] 
     for _, r in inv_df.iterrows(): 
      rows.append(html.Tr([ 
       html.Td(r["Server"], title=str(r["Server"])), html.Td(r["IP"], className="col-ip", title=str(r["IP"])), html.Td(r["Status"], title=str(r.get("Status", ""))), html.Td(r["CPU"], title=str(r["CPU"])), html.Td(r["RAM (GB)"], title=str(r["RAM (GB)"])), html.Td(r["Storage (GB)"], title=str(r["Storage (GB)"])), html.Td(r.get("OS", ""), title=str(r.get("OS", ""))) 
      ])) 
     table_content = dbc.Table([html.Thead(html.Tr(header)), html.Tbody(rows)], striped=True, hover=True, className="table-modern")
     inv_tbl = html.Div(table_content, className="table-scroll-container")
    else:
     header = [html.Th(c, title=c) for c in inv_df.columns] 
     rows = [] 
     for _, r in inv_df.iterrows(): 
      row_data = [html.Td(str(r.get(col, "")), title=str(r.get(col, ""))) for col in inv_df.columns]
      rows.append(html.Tr(row_data))
     table_content = dbc.Table([html.Thead(html.Tr(header)), html.Tbody(rows)], striped=True, hover=True, className="table-modern")
     inv_tbl = html.Div(table_content, className="table-scroll-container") 
  except Exception as e: 
   logger.exception('Inventory query failed: %s', e) 
   inv_tbl = dbc.Alert(f"Error loading inventory: {e}", color='danger') 
  c_fig = build_gauge("vCPU Used", cap["CPU"]["used"], cap["CPU"]["total"], COLORS["cpu"]) 
  r_fig = build_gauge("RAM Used", cap["RAM"]["used"], cap["RAM"]["total"], COLORS["ram"]) 
  s_fig = build_gauge("Storage Used (TB)", cap["STORAGE"]["used"]/1024.0, cap["STORAGE"]["total"]/1024.0, COLORS["storage"]) 
  tc_cpu = build_top_consumers("CPU"); tc_ram = build_top_consumers("RAM"); tc_stg = build_top_consumers("STORAGE") 
  return cards, c_fig, r_fig, s_fig, tc_cpu, tc_ram, tc_stg, ev_tbl, estats_cols, inv_tbl, m_msg, c_msg, d_msg, dl_data, dl_ev_data, datetime.now().strftime("%H:%M:%S") 
 return no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, no_update, m_msg, c_msg, d_msg, dl_data, dl_ev_data, no_update 
@app.callback( 
 [Output("stats-table", "children"), Output("download-stats-csv", "data"), 
  Output("stats-capacity-chart", "figure"), Output("stats-comparison-chart", "figure"), 
  Output("stats-trend-chart", "figure"), Output("stats-heatmap", "figure")], 
 [Input("btn-refresh-stats", "n_clicks"), Input("stats-date-filter", "start_date"), Input("stats-date-filter", "end_date"), Input("btn-export-stats", "n_clicks"), Input("timer", "n_intervals")], 
 prevent_initial_call=False 
) 
def update_stats(n_refresh, start_date, end_date, n_export, n_timer): 
 # Store daily snapshot 
 store_kpi_snapshot() 
 ctx = callback_context 
 trig = ctx.triggered[0]["prop_id"].split(".")[0] if ctx.triggered else "" 
 try: 
  # Retrieve KPI history for table 
  df_table = pd.read_sql(text(""" 
  SELECT snapshot_date::text as "Date", 
         ROUND(cpu_used::numeric, 2) as "CPU Used", 
         ROUND(cpu_total::numeric, 2) as "CPU Total", 
         ROUND(cpu_pct::numeric, 2) as "CPU Used %", 
         ROUND(ram_used::numeric, 2) as "RAM Used (GB)", 
         ROUND(ram_total::numeric, 2) as "RAM Total (GB)", 
         ROUND(ram_pct::numeric, 2) as "RAM Used %", 
         ROUND(storage_used::numeric, 2) as "Storage Used (GB)", 
         ROUND(storage_total::numeric, 2) as "Storage Total (GB)", 
         ROUND(storage_pct::numeric, 2) as "Storage Used %" 
  FROM kpi_daily_snapshot 
  WHERE snapshot_date BETWEEN :start AND :end 
  ORDER BY snapshot_date DESC 
  """), ENGINE, params={"start": start_date, "end": end_date}) 
  # Retrieve raw data for charts 
  df_charts = pd.read_sql(text(""" 
  SELECT snapshot_date, cpu_used, cpu_total, cpu_pct, ram_used, ram_total, ram_pct, 
         storage_used, storage_total, storage_pct 
  FROM kpi_daily_snapshot 
  WHERE snapshot_date BETWEEN :start AND :end 
  ORDER BY snapshot_date 
  """), ENGINE, params={"start": start_date, "end": end_date}) 
  dl_data = no_update 
  if trig == "btn-export-stats" and not df_table.empty: 
   dl_data = dcc.send_data_frame(df_table.to_csv, "kpi_history.csv") 
  if df_table.empty: 
   stats_tbl = html.Div("No historical data available. Data will be collected daily.", className="text-muted p-4 text-center") 
  else: 
   header = [html.Th(c) for c in df_table.columns] 
   rows = [html.Tr([html.Td(r[col]) for col in df_table.columns]) for _, r in df_table.iterrows()] 
   stats_tbl = dbc.Table([html.Thead(html.Tr(header)), html.Tbody(rows)], striped=True, hover=True, className="table-modern") 
  # Build charts 
  capacity_fig = build_capacity_status_chart(df_charts) 
  comparison_fig = build_resource_comparison_chart(df_charts) 
  trend_fig = build_usage_trend_chart(df_charts) 
  heatmap_fig = build_utilization_heatmap(df_charts) 
  return stats_tbl, dl_data, capacity_fig, comparison_fig, trend_fig, heatmap_fig 
 except Exception as e: 
  logger.exception(f"Stats query error: {e}") 
  empty_fig = go.Figure() 
  return dbc.Alert(f"Error loading stats: {e}", color="danger"), no_update, empty_fig, empty_fig, empty_fig, empty_fig 
if __name__ == "__main__": 
 app.run(host="0.0.0.0", port=8052, debug=False)
