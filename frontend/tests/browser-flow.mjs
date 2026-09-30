import {chromium} from '@playwright/test';
import {fileURLToPath} from 'node:url';
import {resolve,dirname} from 'node:path';
const browser=await chromium.launch({channel:'msedge',headless:true});
const page=await browser.newPage({viewport:{width:1440,height:960},acceptDownloads:true});
const errors=[];page.on('pageerror',e=>errors.push(String(e)));
const root=resolve(dirname(fileURLToPath(import.meta.url)),'../..');
try {
 await page.goto('http://127.0.0.1:18080');
 await page.getByLabel('新建项目').fill('浏览器验收-'+Date.now());
 await page.getByRole('button',{name:'创建项目'}).click();
 await page.getByLabel('订单 CSV').setInputFiles(resolve(root,'examples/orders.csv'));
 await page.getByRole('button',{name:'导入并生成质量概况'}).click();
 await page.getByText('笔规范化订单').waitFor();
 await page.getByRole('button',{name:'开始一次分析'}).click();
 await page.getByLabel('分析问题').fill('浏览器全流程验收');
 await page.getByLabel('分析类型').selectOption('ranking');
 await page.getByRole('button',{name:'提交分析任务'}).click();
 await page.getByText('需要明确口径').waitFor({timeout:30000});
 await page.getByRole('button',{name:'前往项目记忆创建口径'}).click();
 await page.getByLabel('业务定义').fill('仅纳入 paid 订单，金额按分计算');
 await page.getByRole('button',{name:'保存为待确认候选'}).click();
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('button',{name:'明确确认'}).click();
 await page.getByText('已确认',{exact:true}).waitFor();
 await page.getByRole('button',{name:/报告详情/}).click();
 await page.getByLabel('选择已确认规则').selectOption({index:1});
 await page.getByRole('button',{name:'确认并继续'}).click();
 await page.getByText('核验通过',{exact:true}).first().waitFor({timeout:60000});
 await page.reload();
 await page.getByText('核验通过',{exact:true}).first().waitFor({timeout:20000});
 await page.getByRole('link',{name:/结果表.csv/}).waitFor();
 // 服务端图表与模板说明：图片须实际解码，说明须写明口径版本。
 await page.getByLabel('分析说明').getByText(/口径版本/).first().waitFor();
 const images=page.locator('.charts img');
 if(await images.count()<1) throw new Error('报告页没有图表');
 if(!(await images.first().evaluate(img=>img.complete&&img.naturalWidth>0))) await images.first().evaluate(img=>new Promise((ok,fail)=>{img.onload=ok;img.onerror=()=>fail(new Error('图表加载失败'));}));
 await page.screenshot({path:resolve(root,'.tmp/report-page.png'),fullPage:true});
 await page.getByRole('button',{name:/项目记忆/}).click();
 await page.getByRole('cell',{name:'payment_time',exact:true}).first().waitFor();
 page.once('dialog',dialog=>dialog.accept());
 await page.getByRole('row',{name:/payment_time/}).getByRole('button',{name:'确认'}).click();
 await page.getByRole('row',{name:/payment_time/}).getByText('已确认',{exact:true}).waitFor();
 await page.screenshot({path:resolve(root,'.tmp/memory-page.png'),fullPage:true});
 for (const width of [1440,390]) { await page.setViewportSize({width,height:850}); for(const section of ['数据工作台','分析任务','报告详情','项目记忆']){await page.getByRole('button',{name:new RegExp(section)}).click();await page.waitForTimeout(250); if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth+3)) throw new Error('水平溢出 '+width+' '+section); } }
 if(errors.length)throw new Error(errors.join(';'));
 console.log('四页面、真实任务、刷新持久化、桌面与移动宽度均通过');
}finally{await browser.close();}
