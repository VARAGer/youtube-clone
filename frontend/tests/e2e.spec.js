const { test, expect } = require('@playwright/test');
const { execFileSync } = require('node:child_process');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

// Uses actual backend, database, object storage and ffmpeg worker. No request mocks.
test('registration → upload → playback → like/comment → privacy and channel', async ({page, browser}, testInfo) => {
  const failures=[];
  page.on('pageerror', error=>failures.push(error.message));
  const unique=`${Date.now()}-${Math.random().toString(16).slice(2,8)}`;
  const email=`browser-${unique}@example.test`, password='Browser-test-8f!';
  const temp=fs.mkdtempSync(path.join(os.tmpdir(),'youtube-e2e-'));
  const video=path.join(temp,'browser-test.mp4');
  if(process.env.E2E_VIDEO)fs.copyFileSync(process.env.E2E_VIDEO,video);
  else execFileSync('ffmpeg',['-hide_banner','-loglevel','error','-y','-f','lavfi','-i','testsrc2=size=320x180:rate=24:duration=3','-f','lavfi','-i','sine=frequency=440:duration=3','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-movflags','+faststart','-shortest',video]);
  let videoId;
  try {
    await page.goto('/');
    await expect(page.getByRole('link',{name:'YouTube, главная'})).toBeVisible();
    await page.locator('#header-actions').getByRole('button',{name:'Войти'}).click();
    await page.getByRole('button',{name:'Создать аккаунт',exact:true}).click();
    await page.getByLabel('Имя канала').fill(`Browser Channel ${unique}`);
    await page.getByLabel('Электронная почта').fill(email);
    await page.getByLabel('Пароль',{exact:true}).fill(password);
    await page.getByRole('button',{name:'Зарегистрироваться',exact:true}).click();
    await expect(page.locator('#dialog')).not.toBeVisible();
    await page.goto('/#/upload');
    await page.getByLabel('Выберите видеофайл').setInputFiles(video);
    await page.getByLabel('Название (обязательно)').fill(`Browser test ${unique}`);
    await page.getByLabel('Описание',{exact:true}).fill('A real end-to-end video upload.');
    const uploaded=page.waitForResponse(r=>r.url().endsWith('/api/videos')&&r.request().method()==='POST');
    await page.getByRole('button',{name:'Загрузить видео',exact:true}).click();
    const response=await uploaded;expect(response.status()).toBe(202);
    const created=await response.json();videoId=created.id;
    await expect(page).toHaveURL(/#\/studio/);
    await expect.poll(async()=>{const r=await page.request.get(`/api/videos/${videoId}`);return (await r.json()).status;},{timeout:120000,intervals:[1000,2000,3000]}).toBe('ready');
    await page.goto(`/#/watch/${videoId}`);
    await expect(page.getByRole('heading',{name:`Browser test ${unique}`})).toBeVisible();
    await expect.poll(()=>page.locator('video').evaluate(v=>v.readyState),{timeout:30000}).toBeGreaterThanOrEqual(1);
    expect(await page.locator('video').evaluate(v=>v.duration)).toBeGreaterThan(2);
    await page.locator('video').evaluate(v=>{v.muted=true;return v.play();});
    await expect.poll(()=>page.locator('video').evaluate(v=>v.currentTime)).toBeGreaterThan(0);
    const like=page.locator('.like-btn');await like.click();await expect(like).toHaveClass(/liked/);await expect(like).toContainText('1');
    await page.getByLabel('Комментарий',{exact:true}).fill('Browser verified comment <script>alert(1)</script>');
    await page.getByRole('button',{name:'Отправить',exact:true}).click();
    await expect(page.locator('.comment-body p')).toHaveText('Browser verified comment <script>alert(1)</script>');
    await expect(page.locator('.comment-body script')).toHaveCount(0);
    await page.screenshot({path:testInfo.outputPath('watch-desktop.png'),fullPage:true});
    await page.setViewportSize({width:390,height:844});
    await page.screenshot({path:testInfo.outputPath('watch-mobile.png'),fullPage:true});
    expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
    await page.locator('#menu').click();
    await expect(page.locator('#sidebar').getByRole('link',{name:'Мои видео',exact:true})).toBeVisible();
    await page.locator('#sidebar').getByRole('link',{name:'Мои видео',exact:true}).click();
    await expect(page).toHaveURL(/#\/studio/);
    await expect(page.locator('#nav-overlay')).not.toBeVisible();
    await page.goto(`/#/watch/${videoId}`);
    await page.setViewportSize({width:1280,height:900});
    await page.getByRole('button',{name:'Изменить',exact:true}).click();
    await page.getByLabel('Доступ',{exact:true}).selectOption('private');
    await page.getByRole('button',{name:'Сохранить',exact:true}).click();
    await expect(page.locator('#dialog')).not.toBeVisible();
    const stranger=await browser.newContext({baseURL:testInfo.project.use.baseURL||process.env.BASE_URL||'http://localhost:8080'});
    try {
      expect((await stranger.request.get(`/api/videos/${videoId}`)).status()).toBe(404);
      expect((await stranger.request.get(`/api/videos/${videoId}/media`,{headers:{Range:'bytes=0-15'}})).status()).toBe(404);
      expect((await stranger.request.get(`/api/videos/${videoId}/thumbnail`)).status()).toBe(404);
      const list=await stranger.request.get('/api/videos');expect((await list.json()).some(v=>v.id===videoId)).toBe(false);
    } finally {await stranger.close();}
    const me=await (await page.request.get('/api/auth/me')).json();
    await page.goto(`/#/channel/${me.id}?tab=playlists`);
    await page.getByRole('button',{name:'Создать плейлист',exact:true}).click();
    await page.getByLabel('Название',{exact:true}).fill(`Playlist ${unique}`);
    await page.getByRole('button',{name:'Создать',exact:true}).click();
    await expect(page.getByRole('heading',{name:`Playlist ${unique}`})).toBeVisible();
    await page.goto('/');
    await page.screenshot({path:testInfo.outputPath('home-desktop.png'),fullPage:true});
    await page.getByLabel('Поиск видео',{exact:true}).fill('unlikely-no-video-'+unique);
    await page.getByRole('button',{name:'Найти',exact:true}).click();
    await expect(page.getByRole('heading',{name:'Ничего не найдено'})).toBeVisible();
    expect(failures).toEqual([]);
  } finally {
    if(videoId)await page.request.delete(`/api/videos/${videoId}`);
    fs.rmSync(temp,{recursive:true,force:true});
  }
});

test('mobile home has no horizontal overflow',async({page},testInfo)=>{
  await page.setViewportSize({width:390,height:844});await page.goto('/');
  await expect(page.locator('.loading')).toHaveCount(0);
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
  await page.screenshot({path:testInfo.outputPath('home-mobile.png'),fullPage:true});
});
