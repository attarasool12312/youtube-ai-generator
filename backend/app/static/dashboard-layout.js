document.addEventListener("DOMContentLoaded", () => {
  const form = document.querySelector('form[action="/jobs"]');
  if (!form) return;
  let o = form.querySelector('[name="orientation"]');
  if (!o) {
    const box = document.createElement("div");
    box.innerHTML = `<label>Orientation</label><select name="orientation">
    <option value="landscape">Landscape — 16:9</option>
    <option value="portrait">Portrait — 9:16</option>
    <option value="square">Square — 1:1</option></select>`;
    form.appendChild(box); o = box.querySelector("select");
  }
  const names=["duration_minutes","language","video_style",
    "narrator_voice","voice_speed","orientation","media_source"];
  const grid=document.createElement("div");
  grid.className="controls-six-grid";
  names.forEach(n=>{
    const f=form.querySelector(`[name="${n}"]`);
    if(f) grid.appendChild(f.parentElement);
  });
  const label=[...form.querySelectorAll("label")]
    .find(x=>x.textContent.trim()==="Additional Voices");
  const extra=label ? label.parentElement : null;
  const button=form.querySelector('button[type="submit"]');
  const submit=button?.parentElement===form ? button : button?.parentElement;
  const anchor=extra || submit;
  anchor && anchor.parentElement===form
    ? form.insertBefore(grid,anchor) : form.appendChild(grid);
  if(extra) grid.after(extra);
  if(submit) (extra || grid).after(submit);
});
