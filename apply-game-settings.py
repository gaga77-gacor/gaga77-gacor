from pathlib import Path
import shutil
import re

ROOT = Path("/storage/emulated/0/Download/game online")

WORKER = ROOT / "worker.js"
ADMIN = ROOT / "admin16.html"

GAMES = [
    "baccarat-idn-1.html",
    "baccarat-idn-2.html",
    "baccarat-idn-3.html",
    "baccarat.html",
    "baccarat2.html",
    "baccarat3.html",
    "spaceman.html",
    "topcard.html",
]

def backup(path):
    if path.exists():
        bak = path.with_name(path.name + ".backup-before-game-settings")
        if not bak.exists():
            shutil.copy2(path, bak)

def patch_worker():
    backup(WORKER)
    s = WORKER.read_text(encoding="utf-8")

    # =========================================================
    # 1. D1 TABLE GAME SETTINGS
    # =========================================================
    marker = """
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS gateway_config (
"""

    insert = r"""
  await env.DB.prepare(`
    CREATE TABLE IF NOT EXISTS game_settings (
      game TEXT PRIMARY KEY,
      mode TEXT NOT NULL DEFAULT 'automatic',
      rtp REAL NOT NULL DEFAULT 96,
      maintenance INTEGER NOT NULL DEFAULT 0,
      updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
    )
  `).run();

  const defaultGames = [
    ["baccarat-idn-1", 96],
    ["baccarat-idn-2", 96],
    ["baccarat-idn-3", 96],
    ["baccarat", 96],
    ["baccarat2", 96],
    ["baccarat3", 96],
    ["spaceman", 96],
    ["topcard", 96]
  ];

  for (const [gameName, defaultRtp] of defaultGames) {
    await env.DB.prepare(`
      INSERT OR IGNORE INTO game_settings
      (game, mode, rtp, maintenance)
      VALUES (?, 'automatic', ?, 0)
    `).bind(gameName, defaultRtp).run();
  }

"""

    if "CREATE TABLE IF NOT EXISTS game_settings" not in s:
        if marker not in s:
            raise SystemExit("Tidak menemukan lokasi gateway_config di worker.js")
        s = s.replace(marker, insert + marker, 1)

    # =========================================================
    # 2. PUBLIC GAME SETTINGS ENDPOINT
    # =========================================================
    marker = """
      /* GAME BALANCE - SERVER AUTHORITATIVE */

      if (path === "/game/adjust" && request.method === "POST") {
"""

    insert = r"""
      /* ===================================================
         GAME SETTINGS - PUBLIC
      =================================================== */

      if (
        path === "/game/settings" &&
        request.method === "GET"
      ) {
        const requested =
          safeString(url.searchParams.get("game"));

        if (!requested) {
          return json({
            success: false,
            message: "Game wajib diisi."
          }, 400);
        }

        const row =
          await env.DB
            .prepare(`
              SELECT
                game,
                mode,
                rtp,
                maintenance,
                updated_at
              FROM game_settings
              WHERE game = ?
              LIMIT 1
            `)
            .bind(requested)
            .first();

        if (!row) {
          return json({
            success: false,
            message: "Pengaturan game tidak ditemukan."
          }, 404);
        }

        return json({
          success: true,
          settings: {
            game: row.game,
            mode: row.mode || "automatic",
            rtp: safeNumber(row.rtp),
            maintenance: Number(row.maintenance) === 1,
            updatedAt: row.updated_at || null
          }
        });
      }


      /* ===================================================
         GAME BALANCE - SERVER AUTHORITATIVE
      =================================================== */

      if (path === "/game/adjust" && request.method === "POST") {
"""

    if 'path === "/game/settings"' not in s:
        if marker not in s:
            raise SystemExit("Tidak menemukan endpoint /game/adjust")
        s = s.replace(marker, insert, 1)

    # =========================================================
    # 3. MAINTENANCE CHECK INSIDE /game/adjust
    #    Only new bets/debits are blocked.
    #    Positive payouts remain possible.
    # =========================================================
    old = """
        const delta = Number(body?.delta);
        const gameName = safeString(body?.game || "game");
        if (!Number.isFinite(delta) || !Number.isInteger(delta) || delta === 0) return json({success:false,message:"Perubahan saldo tidak valid."},400);
"""

    new = """
        const delta = Number(body?.delta);
        const gameName = safeString(body?.game || "game");

        if (!Number.isFinite(delta) || !Number.isInteger(delta) || delta === 0) {
          return json({
            success:false,
            message:"Perubahan saldo tidak valid."
          },400);
        }

        if (delta < 0) {
          const setting =
            await env.DB
              .prepare(`
                SELECT mode, maintenance
                FROM game_settings
                WHERE game = ?
                LIMIT 1
              `)
              .bind(gameName)
              .first();

          if (
            setting &&
            (
              Number(setting.maintenance) === 1 ||
              String(setting.mode || "").toLowerCase() === "maintenance"
            )
          ) {
            return json({
              success:false,
              message:"Game sedang maintenance.",
              game:gameName,
              maintenance:true
            },503);
          }
        }
"""

    if old in s and "Game sedang maintenance." not in s:
        s = s.replace(old, new, 1)

    # =========================================================
    # 4. ADMIN GET SETTINGS
    # =========================================================
    marker = """
      /* ADMIN TRANSACTIONS */
"""

    insert = r"""
      /* ===================================================
         ADMIN GAME SETTINGS
      =================================================== */

      if (
        path === "/admin/game-settings" &&
        request.method === "GET"
      ) {
        if (!isAdmin(request, env)) {
          return json({
            success:false,
            message:"Admin tidak diizinkan."
          },401);
        }

        const result =
          await env.DB
            .prepare(`
              SELECT
                game,
                mode,
                rtp,
                maintenance,
                updated_at
              FROM game_settings
              ORDER BY game ASC
            `)
            .all();

        return json({
          success:true,
          settings:
            (result.results || []).map(row => ({
              game: row.game,
              mode: row.mode || "automatic",
              rtp: safeNumber(row.rtp),
              maintenance: Number(row.maintenance) === 1,
              updatedAt: row.updated_at || null
            }))
        });
      }


      /* ===================================================
         ADMIN UPDATE GAME SETTINGS
      =================================================== */

      if (
        path === "/admin/game-settings" &&
        request.method === "POST"
      ) {
        if (!isAdmin(request, env)) {
          return json({
            success:false,
            message:"Admin tidak diizinkan."
          },401);
        }

        let body;

        try {
          body = await request.json();
        } catch {
          return json({
            success:false,
            message:"JSON tidak valid."
          },400);
        }

        const game =
          safeString(body?.game);

        const mode =
          safeString(body?.mode || "automatic")
            .toLowerCase();

        const rtp =
          Number(body?.rtp);

        const maintenance =
          Boolean(body?.maintenance);

        const allowedModes = [
          "automatic",
          "maintenance"
        ];

        if (!game) {
          return json({
            success:false,
            message:"Game wajib diisi."
          },400);
        }

        if (!allowedModes.includes(mode)) {
          return json({
            success:false,
            message:"Mode tidak valid."
          },400);
        }

        if (
          !Number.isFinite(rtp) ||
          rtp < 50 ||
          rtp > 100
        ) {
          return json({
            success:false,
            message:"RTP harus antara 50 dan 100."
          },400);
        }

        const finalMaintenance =
          maintenance || mode === "maintenance";

        await env.DB.prepare(`
          INSERT INTO game_settings
          (
            game,
            mode,
            rtp,
            maintenance,
            updated_at
          )
          VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
          ON CONFLICT(game)
          DO UPDATE SET
            mode=excluded.mode,
            rtp=excluded.rtp,
            maintenance=excluded.maintenance,
            updated_at=CURRENT_TIMESTAMP
        `)
          .bind(
            game,
            mode,
            rtp,
            finalMaintenance ? 1 : 0
          )
          .run();

        const row =
          await env.DB
            .prepare(`
              SELECT
                game,
                mode,
                rtp,
                maintenance,
                updated_at
              FROM game_settings
              WHERE game = ?
              LIMIT 1
            `)
            .bind(game)
            .first();

        return json({
          success:true,
          message:"Pengaturan game berhasil disimpan.",
          settings:{
            game:row.game,
            mode:row.mode,
            rtp:safeNumber(row.rtp),
            maintenance:Number(row.maintenance) === 1,
            updatedAt:row.updated_at
          }
        });
      }


      /* ADMIN TRANSACTIONS */
"""

    if 'path === "/admin/game-settings"' not in s:
        if marker not in s:
            raise SystemExit("Tidak menemukan ADMIN TRANSACTIONS")
        s = s.replace(marker, insert, 1)

    WORKER.write_text(s, encoding="utf-8")

def patch_admin():
    backup(ADMIN)
    s = ADMIN.read_text(encoding="utf-8")

    # =========================================================
    # NAV BUTTON
    # =========================================================
    nav_marker = """
      <button onclick="showSection('gateway',this)">
        <span class="nav-icon">⚙</span>
        Payment Gateway
      </button>
"""

    nav_insert = nav_marker + """
      <button onclick="showSection('game-settings',this)">
        <span class="nav-icon">🎮</span>
        Pengaturan Game
      </button>
"""

    if "showSection('game-settings',this)" not in s:
        if nav_marker not in s:
            raise SystemExit("Menu Payment Gateway tidak ditemukan")
        s = s.replace(nav_marker, nav_insert, 1)

    # =========================================================
    # GAME SETTINGS SECTION
    # =========================================================
    section_marker = """
      <!-- =================================================
           LIVE CHAT
      ================================================= -->
"""

    section = r"""
      <!-- =================================================
           GAME SETTINGS
      ================================================= -->

      <section
        class="section"
        id="section-game-settings"
      >

        <div class="card">

          <div class="card-head">

            <div>
              <div class="card-title">
                Pengaturan Game
              </div>

              <div class="card-sub">
                Pengaturan global Automatic / RTP / Maintenance
              </div>
            </div>

            <button
              class="small-btn"
              onclick="loadGameSettings()"
            >
              ↻ Refresh
            </button>

          </div>

          <div
            style="
              padding:18px;
              display:grid;
              gap:12px;
            "
          >

            <div
              style="
                padding:12px;
                border-radius:12px;
                background:rgba(124,92,255,.08);
                border:1px solid rgba(124,92,255,.16);
                font-size:12px;
                line-height:1.6;
                color:#aab4c5;
              "
            >
              <b>Automatic</b> menggunakan mekanisme RNG normal.
              RTP adalah parameter konfigurasi game dan tidak digunakan
              untuk menentukan akun tertentu menang atau kalah.
              Maintenance akan menolak taruhan baru dari server.
            </div>

            <div
              id="gameSettingsList"
              style="
                display:grid;
                gap:12px;
              "
            >
              Memuat pengaturan game...
            </div>

          </div>

        </div>

      </section>


"""

    if 'id="section-game-settings"' not in s:
        if section_marker not in s:
            raise SystemExit("Section Live Chat tidak ditemukan")
        s = s.replace(section_marker, section + section_marker, 1)

    # =========================================================
    # TITLE
    # =========================================================
    old = """
    chat:
      "Live Chat"

  };
"""

    new = """
    chat:
      "Live Chat",

    "game-settings":
      "Pengaturan Game"

  };
"""

    if '"game-settings"' not in s:
        if old in s:
            s = s.replace(old, new, 1)

    # =========================================================
    # SHOW SECTION BUTTON MATCH
    # =========================================================
    marker = """
      if(
        section === "chat" &&
        text.includes(
          "live chat"
        )
      ){

        button = btn;

      }

    });
"""

    insert = """
      if(
        section === "game-settings" &&
        text.includes(
          "pengaturan game"
        )
      ){

        button = btn;

      }

    });

"""

    if 'section === "game-settings"' not in s:
        if marker not in s:
            raise SystemExit("showSectionById marker tidak ditemukan")
        s = s.replace(marker, marker.replace("\n    });", "\n" + insert + "    });"), 1)

    # =========================================================
    # LOAD ON REFRESH
    # =========================================================
    old = """
    loadChatUsers(false)

  ]);

}
"""

    new = """
    loadChatUsers(false),

    loadGameSettings()

  ]);

}
"""

    if "loadGameSettings()" not in s:
        if old in s:
            s = s.replace(old, new, 1)

    # =========================================================
    # JS FUNCTIONS
    # =========================================================
    js_marker = """
async function loadGatewayConfig(){
"""

    js = r"""
async function loadGameSettings(){

  const box =
    document.getElementById(
      "gameSettingsList"
    );

  if(!box) return;

  try{

    box.textContent =
      "⏳ Memuat pengaturan...";

    const response =
      await fetch(
        API_URL +
        "/admin/game-settings",
        {
          method:"GET",
          headers:adminHeaders(),
          cache:"no-store"
        }
      );

    const data =
      await response.json();

    if(
      !response.ok ||
      !data.success
    ){
      throw new Error(
        data.message ||
        "Gagal mengambil pengaturan game."
      );
    }

    const settings =
      Array.isArray(data.settings)
      ? data.settings
      : [];

    if(!settings.length){

      box.innerHTML =
        "<div class='card-sub'>Belum ada pengaturan game.</div>";

      return;

    }

    box.innerHTML =
      settings.map(
        setting => {

          const game =
            String(
              setting.game || ""
            );

          const label =
            game
              .replaceAll(
                "-",
                " "
              )
              .replace(
                /\b\w/g,
                x => x.toUpperCase()
              );

          return `
            <div
              style="
                padding:15px;
                border-radius:14px;
                background:#0e1624;
                border:1px solid rgba(255,255,255,.07);
                display:grid;
                gap:10px;
              "
            >

              <div
                style="
                  display:flex;
                  justify-content:space-between;
                  gap:10px;
                  align-items:center;
                "
              >

                <strong>
                  ${escapeHtml(label)}
                </strong>

                <span
                  style="
                    font-size:11px;
                    color:${
                      setting.maintenance
                      ? "#ff7189"
                      : "#62e89a"
                    };
                  "
                >
                  ${
                    setting.maintenance
                    ? "MAINTENANCE"
                    : "ACTIVE"
                  }
                </span>

              </div>

              <div
                style="
                  display:grid;
                  grid-template-columns:
                    repeat(
                      auto-fit,
                      minmax(150px,1fr)
                    );
                  gap:10px;
                "
              >

                <div class="field">

                  <label>MODE</label>

                  <select
                    id="mode-${escapeHtml(game)}"
                    style="
                      width:100%;
                      padding:11px;
                      border-radius:10px;
                      border:1px solid rgba(255,255,255,.08);
                      background:#101827;
                      color:#fff;
                    "
                  >

                    <option
                      value="automatic"
                      ${
                        setting.mode === "automatic"
                        ? "selected"
                        : ""
                      }
                    >
                      Automatic
                    </option>

                    <option
                      value="maintenance"
                      ${
                        setting.mode === "maintenance"
                        ? "selected"
                        : ""
                      }
                    >
                      Maintenance
                    </option>

                  </select>

                </div>

                <div class="field">

                  <label>RTP (%)</label>

                  <input
                    id="rtp-${escapeHtml(game)}"
                    type="number"
                    min="50"
                    max="100"
                    step="0.1"
                    value="${Number(setting.rtp || 96)}"
                  >

                </div>

              </div>

              <button
                class="primary-btn"
                onclick="saveGameSetting('${escapeJs(game)}')"
              >
                💾 Simpan ${escapeHtml(label)}
              </button>

            </div>
          `;
        }
      ).join("");

  }catch(error){

    box.innerHTML =
      `<div style="color:#ff7189">
        ${escapeHtml(
          error.message ||
          "Gagal mengambil pengaturan."
        )}
      </div>`;

  }
}


async function saveGameSetting(game){

  const modeEl =
    document.getElementById(
      "mode-" + game
    );

  const rtpEl =
    document.getElementById(
      "rtp-" + game
    );

  if(!modeEl || !rtpEl){
    return;
  }

  const mode =
    String(
      modeEl.value || "automatic"
    );

  const rtp =
    Number(
      rtpEl.value
    );

  if(
    !Number.isFinite(rtp) ||
    rtp < 50 ||
    rtp > 100
  ){

    showToast(
      "RTP harus antara 50 dan 100.",
      "error"
    );

    return;
  }

  try{

    const response =
      await fetch(
        API_URL +
        "/admin/game-settings",
        {
          method:"POST",
          headers:{
            ...adminHeaders(),
            "Content-Type":
              "application/json"
          },
          body:
            JSON.stringify({
              game,
              mode,
              rtp,
              maintenance:
                mode === "maintenance"
            })
        }
      );

    const data =
      await response.json();

    if(
      !response.ok ||
      !data.success
    ){

      throw new Error(
        data.message ||
        "Gagal menyimpan pengaturan."
      );

    }

    showToast(
      "Pengaturan " +
      game +
      " berhasil disimpan.",
      "success"
    );

    await loadGameSettings();

  }catch(error){

    showToast(
      error.message ||
      "Gagal menyimpan pengaturan.",
      "error"
    );

  }
}


"""

    if "async function loadGameSettings()" not in s:
        if js_marker not in s:
            raise SystemExit("Lokasi JS gateway tidak ditemukan")
        s = s.replace(js_marker, js + js_marker, 1)

    ADMIN.write_text(s, encoding="utf-8")

def patch_game(path):
    if not path.exists():
        return

    backup(path)

    s = path.read_text(encoding="utf-8")

    game_name = path.stem

    # Remove old injected block if script is run twice.
    start = s.find("/* GAME SETTINGS CLIENT */")
    if start != -1:
        end = s.find("/* END GAME SETTINGS CLIENT */", start)
        if end != -1:
            end += len("/* END GAME SETTINGS CLIENT */")
            s = s[:start] + s[end:]

    block = f"""
<script>
/* GAME SETTINGS CLIENT */

(function() {{

  const GAME_SETTINGS_API =
    "https://game-login-api.orkutstok64.workers.dev";

  const GAME_NAME =
    {game_name!r};

  let gameSettings = {{
    game: GAME_NAME,
    mode: "automatic",
    rtp: 96,
    maintenance: false
  }};

  function createGameSettingsBadge() {{

    if(
      document.getElementById(
        "serverGameSettingsBadge"
      )
    ) return;

    const badge =
      document.createElement("div");

    badge.id =
      "serverGameSettingsBadge";

    badge.style.cssText = `
      position:fixed;
      top:10px;
      right:10px;
      z-index:999999;
      padding:7px 10px;
      border-radius:999px;
      background:rgba(8,12,22,.92);
      border:1px solid rgba(255,255,255,.12);
      color:#fff;
      font:700 10px Arial,sans-serif;
      box-shadow:0 5px 20px rgba(0,0,0,.25);
      backdrop-filter:blur(8px);
    `;

    document.body.appendChild(badge);

    updateGameSettingsBadge();
  }}

  function updateGameSettingsBadge() {{

    const badge =
      document.getElementById(
        "serverGameSettingsBadge"
      );

    if(!badge) return;

    if(gameSettings.maintenance) {{

      badge.textContent =
        "🔴 MAINTENANCE";

      badge.style.color =
        "#ff7189";

    }}else{{

      badge.textContent =
        "🟢 AUTO • RTP " +
        Number(gameSettings.rtp || 96) +
        "%";

      badge.style.color =
        "#62e89a";

    }}

  }}

  async function loadServerGameSettings() {{

    try{{

      const response =
        await fetch(
          GAME_SETTINGS_API +
          "/game/settings?game=" +
          encodeURIComponent(
            GAME_NAME
          ),
          {{
            method:"GET",
            cache:"no-store",
            headers:{{
              "Accept":
                "application/json"
            }}
          }}
        );

      const data =
        await response.json();

      if(
        response.ok &&
        data &&
        data.success &&
        data.settings
      ){{

        gameSettings =
          data.settings;

      }}

    }}catch(error){{

      console.warn(
        "GAME SETTINGS ERROR",
        error
      );

    }}

    createGameSettingsBadge();
    updateGameSettingsBadge();

  }}

  /*
   * Server remains authoritative.
   * We only block NEW negative balance adjustments
   * when the server says the game is under maintenance.
   *
   * Positive payouts are never blocked here.
   */

  const originalFetch =
    window.fetch.bind(window);

  window.fetch =
    async function(input, init) {{

      try{{

        const url =
          typeof input === "string"
          ? input
          : input?.url || "";

        if(
          String(url).includes(
            "/game/adjust"
          )
        ){{

          let body =
            init?.body;

          if(
            typeof body === "string"
          ){{

            try{{

              const payload =
                JSON.parse(body);

              const delta =
                Number(
                  payload?.delta
                );

              if(
                delta < 0 &&
                gameSettings.maintenance
              ){{

                return new Response(
                  JSON.stringify({{
                    success:false,
                    message:
                      "Game sedang maintenance.",
                    maintenance:true,
                    game:GAME_NAME
                  }}),
                  {{
                    status:503,
                    headers:{{
                      "Content-Type":
                        "application/json"
                    }}
                  }}
                );

              }}

            }}catch(_error){{}}

          }}

        }}

      }}catch(_error){{}}

      return originalFetch(
        input,
        init
      );

  }};

  window.addEventListener(
    "DOMContentLoaded",
    function(){{

      loadServerGameSettings();

      setInterval(
        loadServerGameSettings,
        30000
      );

    }}
  );

}})();

/* END GAME SETTINGS CLIENT */
</script>
"""

    if "</body>" in s:
        s = s.replace("</body>", block + "\n</body>", 1)
    else:
        s += block

    path.write_text(s, encoding="utf-8")

def main():
    print("=== GAME SETTINGS PATCH ===")

    if not WORKER.exists():
        raise SystemExit("worker.js tidak ditemukan")

    if not ADMIN.exists():
        raise SystemExit("admin16.html tidak ditemukan")

    patch_worker()
    print("[OK] worker.js")

    patch_admin()
    print("[OK] admin16.html")

    for name in GAMES:
        path = ROOT / name

        if path.exists():
            patch_game(path)
            print("[OK]", name)
        else:
            print("[SKIP]", name, "(tidak ditemukan)")

    print()
    print("SELESAI.")
    print("Backup dibuat dengan suffix:")
    print(".backup-before-game-settings")

if __name__ == "__main__":
    main()
