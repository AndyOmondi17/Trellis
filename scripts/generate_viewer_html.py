#!/usr/bin/env python3
"""
scripts/generate_viewer_html.py
Generates a standalone, self-contained interactive HTML Knowledge Graph Explorer
for Trellis (works offline, zero CORS issues, with pan/zoom/search/filter/inspection).
"""

import json
from pathlib import Path

WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
VIZ_DATA_PATH = WORKSPACE_ROOT / "data" / "clean_spec" / "graph_viz_data.json"
WORKSPACE_HTML_PATH = WORKSPACE_ROOT / "knowledge_graph_viewer.html"
ARTIFACT_DIR = Path(r"C:\Users\USER\.gemini\antigravity\brain\f3a1c539-10aa-4373-a2b5-7e78a18860e8")
ARTIFACT_HTML_PATH = ARTIFACT_DIR / "knowledge_graph_viewer.html"

HTML_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Trellis K–16 Mathematics Knowledge Graph Explorer</title>
  <script src="https://www.gstatic.com/antigravity/web/dev/tailwindcss.min.js"></script>
  <style>
    :root {
      --bg: #0b0f19;
      --card-bg: #111827;
      --border: #1f2937;
      --text: #f3f4f6;
      --muted: #9ca3af;
      --accent-blue: #38bdf8;
      --accent-amber: #f59e0b;
      --accent-emerald: #10b981;
      --accent-purple: #a855f7;
    }
    body {
      background-color: var(--bg);
      color: var(--text);
      overflow: hidden;
      font-family: ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    }
    /* Custom Scrollbar */
    ::-webkit-scrollbar { width: 6px; height: 6px; }
    ::-webkit-scrollbar-track { background: rgba(0,0,0,0.2); }
    ::-webkit-scrollbar-thumb { background: #374151; border-radius: 3px; }
    ::-webkit-scrollbar-thumb:hover { background: #4b5563; }
  </style>
</head>
<body class="h-screen w-screen flex flex-col antialiased select-none">

  <!-- TOP NAVIGATION BAR -->
  <header class="h-14 bg-gray-900 border-b border-gray-800 px-4 flex items-center justify-between shrink-0 z-20">
    <div class="flex items-center gap-3">
      <div class="w-8 h-8 rounded-lg bg-gradient-to-tr from-sky-500 to-indigo-600 flex items-center justify-center font-bold text-white shadow-lg shadow-sky-500/20">
        T
      </div>
      <div>
        <h1 class="text-sm font-semibold text-white tracking-wide flex items-center gap-2">
          Trellis Mathematics DAG
          <span class="text-[11px] px-2 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800 font-mono font-normal">K–16 Verified</span>
        </h1>
        <p class="text-[11px] text-gray-400">Mastery Graph • Transitive Reduction • FIRe Encompassing</p>
      </div>
    </div>

    <!-- STATS PILLS -->
    <div class="hidden md:flex items-center gap-2 text-xs">
      <div class="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700/60 text-gray-300">
        <span class="font-bold text-sky-400" id="stat-courses">16</span> Courses
      </div>
      <div class="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700/60 text-gray-300">
        <span class="font-bold text-indigo-400" id="stat-nodes">1,585</span> Topics
      </div>
      <div class="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700/60 text-gray-300">
        <span class="font-bold text-emerald-400" id="stat-kps">4,755</span> KPs
      </div>
      <div class="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700/60 text-gray-300">
        <span class="font-bold text-cyan-400" id="stat-prereqs">1,603</span> Prereqs
      </div>
      <div class="px-2.5 py-1 rounded-md bg-gray-800 border border-gray-700/60 text-gray-300">
        <span class="font-bold text-amber-400" id="stat-fire">3,186</span> FIRe Edges
      </div>
    </div>

    <!-- CONTROLS & SEARCH -->
    <div class="flex items-center gap-2">
      <!-- Search -->
      <div class="relative w-64">
        <input id="search-input" type="text" placeholder="Search topic or trl_id..." 
          class="w-full bg-gray-800 border border-gray-700 rounded-lg px-3 py-1.5 text-xs text-white placeholder-gray-400 focus:outline-none focus:border-sky-500 focus:ring-1 focus:ring-sky-500" />
        <div id="search-dropdown" class="absolute left-0 right-0 top-full mt-1 bg-gray-800 border border-gray-700 rounded-lg shadow-2xl max-h-60 overflow-y-auto hidden z-50"></div>
      </div>

      <!-- Reset Zoom -->
      <button id="btn-fit" class="px-2.5 py-1.5 bg-gray-800 hover:bg-gray-700 text-gray-200 border border-gray-700 rounded-lg text-xs font-medium transition" title="Fit to View">
        Fit View
      </button>
    </div>
  </header>

  <!-- FILTER TOOLBAR -->
  <div class="h-10 bg-gray-950/80 border-b border-gray-800/80 px-4 flex items-center justify-between text-xs overflow-x-auto shrink-0 z-10">
    <!-- Course Quick Chips -->
    <div class="flex items-center gap-1.5 overflow-x-auto py-1" id="course-chips">
      <button class="course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-sky-600 text-white transition shrink-0" data-cid="ALL">All Courses (K–16)</button>
    </div>

    <!-- Edge Toggles -->
    <div class="flex items-center gap-3 shrink-0 pl-4 border-l border-gray-800 text-[11px]">
      <label class="flex items-center gap-1.5 cursor-pointer text-gray-300 hover:text-white">
        <input type="checkbox" id="toggle-prereqs" checked class="rounded border-gray-700 text-sky-500 focus:ring-0">
        <span class="w-2.5 h-0.5 bg-sky-400 rounded-full inline-block"></span>
        Prerequisites
      </label>
      <label class="flex items-center gap-1.5 cursor-pointer text-gray-300 hover:text-white">
        <input type="checkbox" id="toggle-fire" checked class="rounded border-gray-700 text-amber-500 focus:ring-0">
        <span class="w-2.5 h-0.5 bg-amber-400 rounded-full inline-block border-b border-dashed"></span>
        FIRe (Encompassing)
      </label>
    </div>
  </div>

  <!-- MAIN CANVAS & DETAIL PANE WORKSPACE -->
  <div class="flex-1 relative overflow-hidden flex">
    
    <!-- GRAPH CANVAS -->
    <div class="flex-1 relative h-full">
      <canvas id="graph-canvas" class="w-full h-full block cursor-grab active:cursor-grabbing"></canvas>
      
      <!-- Canvas Navigation Overlay Controls -->
      <div class="absolute bottom-4 left-4 bg-gray-900/90 border border-gray-800 rounded-lg p-1.5 flex flex-col gap-1 shadow-xl backdrop-blur-sm">
        <button id="btn-zoom-in" class="w-7 h-7 flex items-center justify-center text-gray-300 hover:text-white hover:bg-gray-800 rounded text-sm font-bold">+</button>
        <button id="btn-zoom-out" class="w-7 h-7 flex items-center justify-center text-gray-300 hover:text-white hover:bg-gray-800 rounded text-sm font-bold">−</button>
      </div>

      <!-- Legend -->
      <div class="absolute bottom-4 right-4 bg-gray-900/90 border border-gray-800 rounded-lg p-2.5 text-[11px] flex flex-col gap-1.5 shadow-xl backdrop-blur-sm pointer-events-none">
        <div class="font-semibold text-gray-300 mb-0.5">Legend</div>
        <div class="flex items-center gap-2 text-gray-400">
          <div class="w-3 h-3 rounded-full bg-sky-500"></div> K–8 Foundation
        </div>
        <div class="flex items-center gap-2 text-gray-400">
          <div class="w-3 h-3 rounded-full bg-indigo-500"></div> High School Bridge (9–12)
        </div>
        <div class="flex items-center gap-2 text-gray-400">
          <div class="w-3 h-3 rounded-full bg-emerald-500"></div> University Calculus / Analysis
        </div>
        <div class="flex items-center gap-2 text-gray-400">
          <div class="w-3 h-3 rounded-full bg-purple-500"></div> University Advanced (LinAlg, DiffEq, AbsAlg)
        </div>
        <div class="flex items-center gap-2 text-gray-400 pt-1 border-t border-gray-800">
          <div class="w-4 h-0.5 bg-sky-400"></div> Hard Prerequisite Edge
        </div>
        <div class="flex items-center gap-2 text-gray-400">
          <div class="w-4 h-0.5 bg-amber-400 border-b border-dashed"></div> Weighted FIRe Encompassing
        </div>
      </div>
    </div>

    <!-- DETAIL DRAWER (RIGHT PANEL) -->
    <aside id="detail-drawer" class="w-96 bg-gray-900 border-l border-gray-800 flex flex-col h-full overflow-hidden shadow-2xl transition-all duration-300">
      <div class="p-4 border-b border-gray-800 flex items-center justify-between bg-gray-900/50">
        <div>
          <span id="detail-id" class="text-[11px] font-mono text-sky-400 bg-sky-950/60 px-2 py-0.5 rounded border border-sky-800">trl_...</span>
          <h2 id="detail-title" class="text-sm font-bold text-white mt-1.5 leading-snug">Select a Topic Node</h2>
        </div>
        <button id="btn-close-drawer" class="text-gray-400 hover:text-white text-lg p-1">✕</button>
      </div>

      <div class="flex-1 overflow-y-auto p-4 space-y-4 text-xs" id="detail-content">
        <p class="text-gray-400 italic">Click any node on the graph canvas or search above to view atomic Knowledge Points, prerequisites, and FIRe repetition weights.</p>
      </div>
    </aside>

  </div>

  <!-- EMBEDDED GRAPH DATA -->
  <script>
    window.__TRELLIS_DATA__ = VIZ_DATA_JSON_PLACEHOLDER;
  </script>

  <!-- APPLICATION LOGIC -->
  <script>
    (function() {
      const data = window.__TRELLIS_DATA__;
      const nodes = data.nodes;
      const courses = data.courses;

      // Quick index maps
      const nodeMap = new Map();
      nodes.forEach(n => nodeMap.set(n.id, n));
      const courseMap = new Map();
      courses.forEach(c => courseMap.set(c.course_id, c));

      // Color Palette by Grade / Branch
      function getNodeColor(gmin, cid) {
        if (gmin <= 2) return '#0284c7'; // Sky-600
        if (gmin <= 5) return '#0ea5e9'; // Sky-500
        if (gmin <= 8) return '#3b82f6'; // Blue-500
        if (cid.startsWith('CRS_ALG') || cid === 'CRS_GEOM' || cid === 'CRS_STAT_HS' || cid === 'CRS_PRECALC') {
          return '#6366f1'; // Indigo-500
        }
        if (cid === 'CRS_CALC1' || cid === 'CRS_CALC2') {
          return '#10b981'; // Emerald-500
        }
        return '#a855f7'; // Purple-500
      }

      // Compute initial topological layout coordinates
      // X = course index, Y = strand rank
      const courseRanks = {};
      courses.forEach((c, idx) => courseRanks[c.course_id] = idx);

      const courseNodes = {};
      courses.forEach(c => courseNodes[c.course_id] = []);
      nodes.forEach(n => {
        if (!courseNodes[n.cid]) courseNodes[n.cid] = [];
        courseNodes[n.cid].push(n);
      });

      // Assign coordinates
      const COL_WIDTH = 320;
      const ROW_HEIGHT = 44;
      courses.forEach(c => {
        const c_nodes = courseNodes[c.course_id] || [];
        const col_idx = courseRanks[c.course_id];
        c_nodes.forEach((n, row_idx) => {
          n.x = (col_idx * COL_WIDTH) + 160;
          n.y = (row_idx * ROW_HEIGHT) + 80;
          n.radius = 9;
          n.color = getNodeColor(n.gmin, n.cid);
        });
      });

      // Canvas setup
      const canvas = document.getElementById('graph-canvas');
      const ctx = canvas.getContext('2d');
      let width = canvas.width = canvas.parentElement.clientWidth;
      let height = canvas.height = canvas.parentElement.clientHeight;

      window.addEventListener('resize', () => {
        width = canvas.width = canvas.parentElement.clientWidth;
        height = canvas.height = canvas.parentElement.clientHeight;
        render();
      });

      // Camera State
      let camera = { x: 0, y: 0, zoom: 0.45 };
      let isDragging = false;
      let lastMouse = { x: 0, y: 0 };
      let selectedNode = null;
      let hoveredNode = null;
      let activeCourseFilter = 'ALL';

      // Controls
      const togglePrereqs = document.getElementById('toggle-prereqs');
      const toggleFire = document.getElementById('toggle-fire');
      togglePrereqs.addEventListener('change', render);
      toggleFire.addEventListener('change', render);

      // Fit View
      function fitView() {
        if (nodes.length === 0) return;
        let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
        const visibleNodes = activeCourseFilter === 'ALL' ? nodes : nodes.filter(n => n.cid === activeCourseFilter);
        visibleNodes.forEach(n => {
          minX = Math.min(minX, n.x);
          maxX = Math.max(maxX, n.x);
          minY = Math.min(minY, n.y);
          maxY = Math.max(maxY, n.y);
        });
        const graphW = (maxX - minX) || 1000;
        const graphH = (maxY - minY) || 1000;
        const zoomX = (width - 120) / graphW;
        const zoomY = (height - 120) / graphH;
        camera.zoom = Math.max(0.08, Math.min(1.2, Math.min(zoomX, zoomY)));
        camera.x = width / 2 - ((minX + maxX) / 2) * camera.zoom;
        camera.y = height / 2 - ((minY + maxY) / 2) * camera.zoom;
        render();
      }

      document.getElementById('btn-fit').addEventListener('click', fitView);
      document.getElementById('btn-zoom-in').addEventListener('click', () => {
        camera.zoom = Math.min(3.0, camera.zoom * 1.25);
        render();
      });
      document.getElementById('btn-zoom-out').addEventListener('click', () => {
        camera.zoom = Math.max(0.05, camera.zoom / 1.25);
        render();
      });

      // Canvas Interaction
      canvas.addEventListener('mousedown', (e) => {
        isDragging = true;
        lastMouse = { x: e.clientX, y: e.clientY };
      });
      window.addEventListener('mouseup', () => isDragging = false);
      canvas.addEventListener('mousemove', (e) => {
        if (isDragging) {
          camera.x += e.clientX - lastMouse.x;
          camera.y += e.clientY - lastMouse.y;
          lastMouse = { x: e.clientX, y: e.clientY };
          render();
        } else {
          // Hover detection
          const rect = canvas.getBoundingClientRect();
          const mx = (e.clientX - rect.left - camera.x) / camera.zoom;
          const my = (e.clientY - rect.top - camera.y) / camera.zoom;
          let found = null;
          for (let i = nodes.length - 1; i >= 0; i--) {
            const n = nodes[i];
            if (activeCourseFilter !== 'ALL' && n.cid !== activeCourseFilter) continue;
            const distSq = (n.x - mx) ** 2 + (n.y - my) ** 2;
            if (distSq <= (n.radius + 6) ** 2) {
              found = n;
              break;
            }
          }
          if (found !== hoveredNode) {
            hoveredNode = found;
            canvas.style.cursor = found ? 'pointer' : 'grab';
            render();
          }
        }
      });

      canvas.addEventListener('wheel', (e) => {
        e.preventDefault();
        const zoomFactor = e.deltaY < 0 ? 1.12 : 0.88;
        const newZoom = Math.max(0.05, Math.min(3.5, camera.zoom * zoomFactor));
        const rect = canvas.getBoundingClientRect();
        const mouseX = e.clientX - rect.left;
        const mouseY = e.clientY - rect.top;
        camera.x = mouseX - (mouseX - camera.x) * (newZoom / camera.zoom);
        camera.y = mouseY - (mouseY - camera.y) * (newZoom / camera.zoom);
        camera.zoom = newZoom;
        render();
      }, { passive: false });

      canvas.addEventListener('click', (e) => {
        if (hoveredNode) {
          selectNode(hoveredNode);
        }
      });

      // Selection & Detail Drawer
      function selectNode(n) {
        selectedNode = n;
        render();
        updateDrawer(n);
      }

      function updateDrawer(n) {
        const drawer = document.getElementById('detail-drawer');
        const dId = document.getElementById('detail-id');
        const dTitle = document.getElementById('detail-title');
        const dContent = document.getElementById('detail-content');

        dId.textContent = n.id;
        dTitle.textContent = n.title;

        const cInfo = courseMap.get(n.cid) || { title: n.cid };
        const prereqList = n.pre.map(pid => nodeMap.get(pid)).filter(Boolean);
        const encList = n.enc.map(e => ({ node: nodeMap.get(e.id), w: e.w, non_anc: e.non_anc })).filter(e => Boolean(e.node));

        let html = `
          <div class="bg-gray-800/60 p-3 rounded-lg border border-gray-700/60 space-y-1.5">
            <div class="flex justify-between items-center">
              <span class="text-gray-400">Course:</span>
              <span class="font-medium text-white">${cInfo.title}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-gray-400">Grade Level:</span>
              <span class="font-mono text-gray-200">Grades ${n.gmin}–${n.gmax}</span>
            </div>
            <div class="flex justify-between items-center">
              <span class="text-gray-400">Key Prerequisite:</span>
              <span class="font-mono ${n.kpid ? 'text-sky-400 cursor-pointer hover:underline' : 'text-gray-500'}" id="link-key-prereq">
                ${n.kpid || 'None (K-2 Root)'}
              </span>
            </div>
          </div>

          <!-- KNOWLEDGE POINTS STAIRCASE -->
          <div class="space-y-2">
            <div class="font-semibold text-gray-200 flex items-center justify-between">
              <span>Knowledge Points (${n.kps.length})</span>
              <span class="text-[10px] text-emerald-400 bg-emerald-950/70 border border-emerald-800 px-1.5 py-0.5 rounded">N=3 Gate</span>
            </div>
            ${n.kps.map(kp => `
              <div class="bg-gray-800/40 border border-gray-700/50 rounded-lg p-2.5 space-y-1">
                <div class="flex items-center justify-between text-[11px]">
                  <span class="font-mono font-semibold text-sky-400">KP_${kp.idx}</span>
                  <span class="text-gray-400">${kp.tier}</span>
                </div>
                <p class="text-gray-300 text-[11px] leading-relaxed">${kp.focus}</p>
                <p class="text-[10px] text-gray-400 italic bg-gray-900/60 p-1.5 rounded border border-gray-800">${kp.rule}</p>
              </div>
            `).join('')}
          </div>

          <!-- PREREQUISITES -->
          <div class="space-y-1.5">
            <div class="font-semibold text-gray-200">Prerequisites (${prereqList.length})</div>
            ${prereqList.length === 0 ? '<p class="text-gray-500 italic">None (Foundation Entry Skill)</p>' : `
              <div class="flex flex-wrap gap-1.5">
                ${prereqList.map(p => `
                  <button class="node-chip text-left px-2 py-1 rounded bg-sky-950/60 border border-sky-800/80 hover:bg-sky-900 text-sky-200 text-[11px] transition" data-nid="${p.id}">
                    <span class="font-mono text-sky-400 block text-[10px]">${p.id}</span>
                    <span class="truncate block max-w-[280px]">${p.title}</span>
                  </button>
                `).join('')}
              </div>
            `}
          </div>

          <!-- ENCOMPASSED SKILLS (FIRe) -->
          <div class="space-y-1.5">
            <div class="font-semibold text-gray-200">Encompassed Skills (${encList.length})</div>
            ${encList.length === 0 ? '<p class="text-gray-500 italic">No component skills recorded</p>' : `
              <div class="space-y-1">
                ${encList.map(e => `
                  <button class="node-chip w-full text-left p-2 rounded bg-amber-950/40 border border-amber-800/60 hover:bg-amber-900/50 text-amber-200 text-[11px] flex items-center justify-between transition" data-nid="${e.node.id}">
                    <div>
                      <span class="font-mono text-amber-400 block text-[10px]">${e.node.id}</span>
                      <span class="truncate block max-w-[200px] text-gray-300">${e.node.title}</span>
                    </div>
                    <div class="text-right shrink-0">
                      <span class="px-1.5 py-0.5 rounded bg-amber-900/80 text-amber-300 font-mono text-[10px]">w=${e.w.toFixed(2)}</span>
                      ${e.non_anc ? '<span class="block text-[9px] text-amber-400">Lateral</span>' : ''}
                    </div>
                  </button>
                `).join('')}
              </div>
            `}
          </div>
        `;

        dContent.innerHTML = html;

        // Attach chip listeners
        dContent.querySelectorAll('.node-chip').forEach(btn => {
          btn.addEventListener('click', () => {
            const targetId = btn.getAttribute('data-nid');
            const targetNode = nodeMap.get(targetId);
            if (targetNode) {
              selectNode(targetNode);
              // Center camera on target
              camera.x = width / 2 - targetNode.x * camera.zoom;
              camera.y = height / 2 - targetNode.y * camera.zoom;
              render();
            }
          });
        });

        const keyPrereqLink = document.getElementById('link-key-prereq');
        if (keyPrereqLink && n.kpid && nodeMap.has(n.kpid)) {
          keyPrereqLink.addEventListener('click', () => {
            const targetNode = nodeMap.get(n.kpid);
            if (targetNode) {
              selectNode(targetNode);
              camera.x = width / 2 - targetNode.x * camera.zoom;
              camera.y = height / 2 - targetNode.y * camera.zoom;
              render();
            }
          });
        }
      }

      document.getElementById('btn-close-drawer').addEventListener('click', () => {
        selectedNode = null;
        render();
        document.getElementById('detail-title').textContent = "Select a Topic Node";
        document.getElementById('detail-id').textContent = "trl_...";
        document.getElementById('detail-content').innerHTML = '<p class="text-gray-400 italic">Click any node on the graph canvas or search above to view atomic Knowledge Points, prerequisites, and FIRe repetition weights.</p>';
      });

      // Search
      const searchInput = document.getElementById('search-input');
      const searchDropdown = document.getElementById('search-dropdown');
      searchInput.addEventListener('input', () => {
        const q = searchInput.value.trim().toLowerCase();
        if (q.length < 2) {
          searchDropdown.classList.add('hidden');
          return;
        }
        const matches = nodes.filter(n => n.id.toLowerCase().includes(q) || n.title.toLowerCase().includes(q)).slice(0, 10);
        if (matches.length === 0) {
          searchDropdown.innerHTML = '<div class="p-2.5 text-xs text-gray-400 italic">No matching topics found</div>';
        } else {
          searchDropdown.innerHTML = matches.map(n => `
            <div class="search-item p-2 hover:bg-gray-700 cursor-pointer border-b border-gray-700/50 last:border-0" data-nid="${n.id}">
              <span class="font-mono text-sky-400 text-[10px] block">${n.id}</span>
              <span class="text-white text-xs block truncate">${n.title}</span>
            </div>
          `).join('');
          searchDropdown.querySelectorAll('.search-item').forEach(el => {
            el.addEventListener('click', () => {
              const targetNode = nodeMap.get(el.getAttribute('data-nid'));
              if (targetNode) {
                selectNode(targetNode);
                camera.x = width / 2 - targetNode.x * camera.zoom;
                camera.y = height / 2 - targetNode.y * camera.zoom;
                camera.zoom = Math.max(0.6, camera.zoom);
                render();
                searchDropdown.classList.add('hidden');
                searchInput.value = '';
              }
            });
          });
        }
        searchDropdown.classList.remove('hidden');
      });

      document.addEventListener('click', (e) => {
        if (!searchInput.contains(e.target) && !searchDropdown.contains(e.target)) {
          searchDropdown.classList.add('hidden');
        }
      });

      // Course Filter Chips
      const courseChipsContainer = document.getElementById('course-chips');
      courses.forEach(c => {
        const btn = document.createElement('button');
        btn.className = "course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-gray-800 text-gray-300 hover:bg-gray-700 transition shrink-0";
        btn.setAttribute('data-cid', c.course_id);
        btn.textContent = `${c.title.split(':')[0]} (${c.grade_band})`;
        btn.addEventListener('click', () => {
          document.querySelectorAll('.course-chip').forEach(b => {
            b.className = "course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-gray-800 text-gray-300 hover:bg-gray-700 transition shrink-0";
          });
          btn.className = "course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-sky-600 text-white transition shrink-0";
          activeCourseFilter = c.course_id;
          fitView();
        });
        courseChipsContainer.appendChild(btn);
      });

      document.querySelector('.course-chip[data-cid="ALL"]').addEventListener('click', function() {
        document.querySelectorAll('.course-chip').forEach(b => {
          b.className = "course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-gray-800 text-gray-300 hover:bg-gray-700 transition shrink-0";
        });
        this.className = "course-chip px-2 py-0.5 rounded text-[11px] font-medium bg-sky-600 text-white transition shrink-0";
        activeCourseFilter = 'ALL';
        fitView();
      });

      // RENDER FUNCTION
      function render() {
        ctx.clearRect(0, 0, width, height);

        ctx.save();
        ctx.translate(camera.x, camera.y);
        ctx.scale(camera.zoom, camera.zoom);

        const showPrereqs = togglePrereqs.checked;
        const showFire = toggleFire.checked;

        // Draw Course Columns / Background Rails
        courses.forEach(c => {
          if (activeCourseFilter !== 'ALL' && c.course_id !== activeCourseFilter) return;
          const col_idx = courseRanks[c.course_id];
          const colX = col_idx * COL_WIDTH;
          
          ctx.fillStyle = 'rgba(31, 41, 55, 0.25)';
          ctx.fillRect(colX + 20, 20, COL_WIDTH - 40, (courseNodes[c.course_id]?.length || 10) * ROW_HEIGHT + 80);

          ctx.strokeStyle = 'rgba(55, 65, 81, 0.4)';
          ctx.lineWidth = 1;
          ctx.strokeRect(colX + 20, 20, COL_WIDTH - 40, (courseNodes[c.course_id]?.length || 10) * ROW_HEIGHT + 80);

          ctx.fillStyle = '#9ca3af';
          ctx.font = 'bold 14px ui-sans-serif, system-ui';
          ctx.fillText(c.title.split(':')[0], colX + 35, 50);
          ctx.fillStyle = '#6b7280';
          ctx.font = '11px ui-sans-serif, system-ui';
          ctx.fillText(`${c.grade_band} • ${(courseNodes[c.course_id] || []).length} topics`, colX + 35, 68);
        });

        // DRAW EDGES
        const selPrereqSet = selectedNode ? new Set(selectedNode.pre) : null;
        const selEncSet = selectedNode ? new Set(selectedNode.enc.map(e => e.id)) : null;

        nodes.forEach(n => {
          if (activeCourseFilter !== 'ALL' && n.cid !== activeCourseFilter) return;

          // Prerequisite Edges (Solid Sky Blue)
          if (showPrereqs) {
            n.pre.forEach(pid => {
              const p = nodeMap.get(pid);
              if (!p) return;
              if (activeCourseFilter !== 'ALL' && p.cid !== activeCourseFilter) return;

              const isHighlighted = (selectedNode && (selectedNode.id === n.id || selectedNode.id === p.id));
              const isDimmed = selectedNode && !isHighlighted;

              ctx.beginPath();
              ctx.moveTo(p.x, p.y);
              ctx.lineTo(n.x, n.y);

              if (isHighlighted) {
                ctx.strokeStyle = '#38bdf8';
                ctx.lineWidth = 2.5;
              } else if (isDimmed) {
                ctx.strokeStyle = 'rgba(56, 189, 248, 0.08)';
                ctx.lineWidth = 0.8;
              } else {
                ctx.strokeStyle = 'rgba(56, 189, 248, 0.3)';
                ctx.lineWidth = 1;
              }
              ctx.stroke();
            });
          }

          // FIRe Encompassing Edges (Dashed Amber)
          if (showFire) {
            n.enc.forEach(e => {
              const child = nodeMap.get(e.id);
              if (!child) return;
              if (activeCourseFilter !== 'ALL' && child.cid !== activeCourseFilter) return;

              const isHighlighted = (selectedNode && (selectedNode.id === n.id || selectedNode.id === child.id));
              const isDimmed = selectedNode && !isHighlighted;

              ctx.beginPath();
              ctx.setLineDash([4, 4]);
              ctx.moveTo(n.x, n.y);
              ctx.lineTo(child.x, child.y);

              if (isHighlighted) {
                ctx.strokeStyle = '#f59e0b';
                ctx.lineWidth = 2.5;
              } else if (isDimmed) {
                ctx.strokeStyle = 'rgba(245, 158, 11, 0.06)';
                ctx.lineWidth = 0.8;
              } else {
                ctx.strokeStyle = 'rgba(245, 158, 11, 0.25)';
                ctx.lineWidth = 1;
              }
              ctx.stroke();
              ctx.setLineDash([]);
            });
          }
        });

        // DRAW NODES
        nodes.forEach(n => {
          if (activeCourseFilter !== 'ALL' && n.cid !== activeCourseFilter) return;

          const isSelected = selectedNode && selectedNode.id === n.id;
          const isHovered = hoveredNode && hoveredNode.id === n.id;
          const isRelated = selectedNode && (selPrereqSet.has(n.id) || selEncSet.has(n.id));
          const isDimmed = selectedNode && !isSelected && !isRelated;

          ctx.beginPath();
          ctx.arc(n.x, n.y, isSelected ? n.radius + 3 : (isHovered ? n.radius + 2 : n.radius), 0, Math.PI * 2);

          if (isSelected) {
            ctx.fillStyle = '#ffffff';
            ctx.shadowColor = '#38bdf8';
            ctx.shadowBlur = 16;
          } else if (isHovered) {
            ctx.fillStyle = '#f8fafc';
            ctx.shadowColor = '#38bdf8';
            ctx.shadowBlur = 10;
          } else if (isRelated) {
            ctx.fillStyle = selPrereqSet.has(n.id) ? '#38bdf8' : '#f59e0b';
            ctx.shadowColor = ctx.fillStyle;
            ctx.shadowBlur = 8;
          } else if (isDimmed) {
            ctx.fillStyle = 'rgba(75, 85, 99, 0.3)';
            ctx.shadowBlur = 0;
          } else {
            ctx.fillStyle = n.color;
            ctx.shadowBlur = 0;
          }

          ctx.fill();
          ctx.shadowBlur = 0;

          // Node Border
          ctx.lineWidth = isSelected ? 3 : 1.5;
          ctx.strokeStyle = isSelected ? '#38bdf8' : (isRelated ? '#ffffff' : '#111827');
          ctx.stroke();

          // Node Labels at comfortable zoom levels
          if (camera.zoom > 0.45 || isSelected || isHovered) {
            ctx.font = isSelected ? 'bold 11px ui-sans-serif, system-ui' : '10px ui-sans-serif, system-ui';
            ctx.fillStyle = isSelected ? '#ffffff' : (isDimmed ? 'rgba(156, 163, 175, 0.3)' : '#e5e7eb');
            ctx.fillText(n.title.length > 28 ? n.title.slice(0, 26) + '...' : n.title, n.x + n.radius + 6, n.y + 3.5);
          }
        });

        ctx.restore();
      }

      // Initial Fit
      fitView();
      // Select the first root topic as default
      if (nodes.length > 0) {
        selectNode(nodes[0]);
      }
    })();
  </script>
</body>
</html>
"""

def generate_viewer():
    if not VIZ_DATA_PATH.exists():
        raise FileNotFoundError(f"Missing {VIZ_DATA_PATH}")

    raw_json = VIZ_DATA_PATH.read_text(encoding="utf-8")
    html_content = HTML_TEMPLATE.replace("VIZ_DATA_JSON_PLACEHOLDER", raw_json)

    # Write to workspace
    WORKSPACE_HTML_PATH.write_text(html_content, encoding="utf-8")
    print(f"[OK] Generated workspace viewer at {WORKSPACE_HTML_PATH}")

    # Write to brain artifact directory
    ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    ARTIFACT_HTML_PATH.write_text(html_content, encoding="utf-8")
    print(f"[OK] Generated brain artifact viewer at {ARTIFACT_HTML_PATH}")

if __name__ == "__main__":
    generate_viewer()
