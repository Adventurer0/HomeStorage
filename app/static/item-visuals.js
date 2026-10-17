'use strict';

// Decorative, local-only illustrations. No item data or image requests leave the NAS.
// Match the name before tags/category so a broad category cannot override a clear name.
const ItemVisuals = (() => {
  const drawings = {
    box: ['日常物品', 'slate', '<path d="m12 3 9 5-9 5-9-5 9-5Zm-9 5v10l9 5 9-5V8M12 13v10M7.5 5.5l9 5"/>'],
    cable: ['线材与连接', 'blue', '<rect x="3" y="2" width="6" height="5" rx="1"/><path d="M5 2V1m2 1V1M6 7v7a4 4 0 0 0 8 0v-4a3 3 0 0 1 6 0v7"/><rect x="17" y="17" width="6" height="4" rx="1"/><path d="M19 21v2m2-2v2"/>'],
    charger: ['充电与电源', 'blue', '<rect x="5" y="6" width="14" height="15" rx="3"/><path d="M9 6V2m6 4V2m-2 8-3 4h4l-3 4"/>'],
    battery: ['电池与储能', 'green', '<rect x="2" y="6" width="18" height="12" rx="2"/><path d="M22 10v4M6 10v4m-2-2h4m5 0h3"/>'],
    headphones: ['随身音频', 'violet', '<path d="M4 14v-3a8 8 0 0 1 16 0v3"/><rect x="3" y="12" width="5" height="9" rx="2"/><rect x="16" y="12" width="5" height="9" rx="2"/>'],
    phone: ['随身设备', 'blue', '<rect x="6" y="2" width="12" height="20" rx="3"/><path d="M10 5h4m-3 14h2"/>'],
    computer: ['电脑与办公', 'blue', '<rect x="4" y="3" width="16" height="13" rx="2"/><path d="m4 16-2 4h20l-2-4M9 20h6"/>'],
    camera: ['影像设备', 'violet', '<path d="m8 6 2-3h4l2 3h4a2 2 0 0 1 2 2v11H2V8a2 2 0 0 1 2-2h4Z"/><circle cx="12" cy="12" r="4"/><path d="M18 9h1"/>'],
    electronics: ['数码家电', 'blue', '<rect x="4" y="3" width="16" height="14" rx="2"/><path d="M8 21h8m-4-4v4M8 7h8m-8 4h4"/>'],
    medicine: ['健康常备', 'rose', '<path d="m8 3 13 13a4 4 0 0 1-5 5L3 8a4 4 0 0 1 5-5ZM7 12l5-5"/><path d="M16 4h6m-3-3v6"/>'],
    firstaid: ['日常防护', 'rose', '<rect x="3" y="6" width="18" height="15" rx="3"/><path d="M8 6V3h8v3m-4 5v6m-3-3h6"/>'],
    fitness: ['运动与健身', 'orange', '<path d="m6 6 12 12M3 5l4-4m-6 6 4-4m12 20 6-6M3 11l8-8m2 18 8-8M1 7l6 6m10-2 6 6"/>'],
    outdoor: ['户外装备', 'green', '<path d="m12 3 10 18H2L12 3Zm0 0v18m0-10 6 10m-6-10-6 10M4 7l2 1m13-4 1-2"/>'],
    tool: ['家中工具', 'sand', '<path d="m14 6 4 4 4-4a7 7 0 0 1-9 9L6 22l-4-4 7-7a7 7 0 0 1 9-9l-4 4Z"/><path d="m5 18 1 1"/>'],
    cleaning: ['清洁好物', 'teal', '<path d="M8 2h8v4H8Zm1 4v4l-4 4v8h14v-8l-4-4V6M6 15h12m-6 3v1M16 3h4v3"/>'],
    care: ['个人护理', 'rose', '<path d="M8 7h8v15H8ZM10 7V3h4v4m-4-4V1h7m-5 11v5m-2-3h4"/>'],
    clothing: ['衣物与织物', 'violet', '<path d="m8 3-6 4 3 6 3-2v10h8V11l3 2 3-6-6-4c0 4-8 4-8 0Z"/>'],
    shoes: ['鞋履配件', 'sand', '<path d="M3 12V5h5l3 8 10 4v4H2v-6l1-3Zm0 5h18m-10-5 2-2m1 4 2-2"/>'],
    food: ['食物与零食', 'orange', '<path d="M12 7c-5-6-12 1-8 9 2 5 5 6 8 4 3 2 6 1 8-4 4-8-3-15-8-9Zm0 0c-1-4 2-6 5-6 0 4-2 6-5 6Z"/>'],
    drink: ['饮品与茶咖', 'sand', '<path d="M4 8h13v8a5 5 0 0 1-5 5H9a5 5 0 0 1-5-5V8Zm13 1h2a3 3 0 1 1-2 6M7 2v3m4-4v4m4-3v3"/>'],
    kitchen: ['厨房好物', 'orange', '<path d="M5 3v6a3 3 0 0 0 6 0V3M8 3v19M19 3c-4 4-4 10 0 10V3Zm0 10v9"/>'],
    book: ['书籍与阅读', 'teal', '<path d="M12 5C9 2 4 2 2 3v17c3-1 7-1 10 1 3-2 7-2 10-1V3c-2-1-7-1-10 2Zm0 0v16M5 7h3m-3 4h3m8-4h3m-3 4h3"/>'],
    stationery: ['文具与记录', 'blue', '<path d="m16 2 6 6-13 13-7 1 1-7L16 2Zm-2 2 6 6M3 15l6 6M11 7 7 3l-5 5 4 4m6 6 4 4 6-6-4-4"/>'],
    toy: ['童趣收藏', 'orange', '<path d="M8 7 4 5a3 3 0 1 1 5-3l3 3 3-3a3 3 0 1 1 5 3l-4 2M5 11a7 7 0 0 1 14 0v6a5 5 0 0 1-5 5h-4a5 5 0 0 1-5-5v-6Z"/><path d="M9 11h.01M15 11h.01m-5 5 2 1 2-1"/>'],
    pet: ['宠物日常', 'sand', '<ellipse cx="6" cy="6" rx="2" ry="3"/><ellipse cx="18" cy="6" rx="2" ry="3"/><ellipse cx="2.5" cy="12" rx="1.5" ry="2.5"/><ellipse cx="21.5" cy="12" rx="1.5" ry="2.5"/><path d="M6 18c0-3 3-7 6-7s6 4 6 7-4 3-6 2c-2 1-6 1-6-2Z"/>'],
    plant: ['绿意与园艺', 'green', '<path d="M12 13C4 13 3 7 3 4c8 0 10 3 9 9Zm0 0c0-8 5-10 9-10 0 7-4 10-9 10Zm0 0v5M7 18h10l-1 4H8l-1-4Z"/>'],
  };
  // English words have boundaries: e.g. "pineapple" is food, never an Apple device.
  const rules = [
    ['cable', /数据线|數據線|充电线|充電線|转接线|轉接線|延长线|延長線|电源线|電源線|网线|網線|连接线|連接線|\b(?:cables?|cords?)\b/i],
    ['charger', /充电器|充電器|充电头|充電頭|充电宝|充電寶|移动电源|移動電源|电源适配器|電源適配器|插排|插线板|\b(?:chargers?|power[ -]?banks?|power[ -]?adapters?)\b/i],
    ['battery', /电池|電池|蓄电|蓄電|\b(?:batter(?:y|ies)|accumulators?)\b/i],
    ['headphones', /耳机|耳機|耳麦|耳麥|音箱|音响|音響|\b(?:headphones?|headsets?|earphones?|earbuds?|airpods?|speakers?)\b/i],
    ['phone', /手机|手機|平板电脑|平板電腦|\b(?:phones?|smartphones?|iphone|ipad|tablets?)\b/i],
    ['computer', /笔记本电脑|筆記本電腦|电脑|電腦|键盘|鍵盤|鼠标|鼠標|显示器|顯示器|\b(?:laptops?|computers?|macbooks?|keyboards?|monitors?|mouse|mice)\b/i],
    ['camera', /相机|相機|摄像|攝像|镜头|鏡頭|三脚架|三腳架|\b(?:cameras?|lenses?|tripods?)\b/i],
    ['medicine', /药片|藥片|胶囊|膠囊|感冒药|感冒藥|退烧药|退燒藥|止痛药|止痛藥|眼药|眼藥|药膏|藥膏|布洛芬|维生素|維生素|阿莫西林|\b(?:medicines?|medications?|pills?|ibuprofen|vitamins?|capsules?)\b/i],
    ['firstaid', /口罩|创可贴|創可貼|绷带|繃帶|纱布|紗布|急救|体温计|體溫計|消毒棉|\b(?:masks?|bandages?|first[ -]?aid|thermometers?)\b/i],
    ['pet', /猫粮|貓糧|狗粮|狗糧|猫砂|貓砂|宠物|寵物|猫玩具|狗玩具|\b(?:pet[ -]?(?:food|toys?)|cat[ -]?(?:food|litter)|dog[ -]?food)\b/i],
    ['fitness', /哑铃|啞鈴|杠铃|槓鈴|瑜伽|跳绳|跳繩|弹力带|彈力帶|壶铃|壺鈴|健身|篮球|籃球|足球|羽毛球|球拍|\b(?:dumbbells?|barbells?|kettlebells?|yoga|fitness|basketballs?|footballs?|rackets?)\b/i],
    ['outdoor', /帐篷|帳篷|睡袋|登山|露营|露營|野餐|登山杖|\b(?:tents?|sleeping[ -]?bags?|camping|hiking)\b/i],
    ['tool', /螺丝刀|螺絲刀|扳手|钳子|鉗子|电钻|電鑽|锤子|錘子|工具|打气筒|打氣筒|卷尺|\b(?:tools?|screwdrivers?|wrenches?|pliers?|drills?|hammers?|pumps?)\b/i],
    ['cleaning', /洗衣液|洗衣粉|洗洁精|洗潔精|清洁剂|清潔劑|清洁液|清潔液|消毒液|洁厕|潔廁|拖把|扫帚|掃帚|抹布|垃圾袋|\b(?:detergents?|cleaners?|mops?|brooms?|trash[ -]?bags?)\b/i],
    ['care', /洗发水|洗髮水|沐浴露|牙刷|牙膏|洗面奶|护肤|護膚|面霜|防晒|防曬|洗手液|毛巾|卫生纸|衛生紙|纸巾|紙巾|\b(?:shampoo|toothbrush(?:es)?|toothpaste|skincare|towels?|tissues?|soap)\b/i],
    ['shoes', /运动鞋|運動鞋|皮鞋|拖鞋|凉鞋|涼鞋|靴子|鞋子|鞋履|\b(?:shoes?|sneakers?|slippers?|sandals?|boots?)\b/i],
    ['clothing', /衣服|外套|衬衫|襯衫|短袖|长袖|長袖|羽绒服|羽絨服|裤子|褲子|袜子|襪子|围巾|圍巾|手套|床单|床單|被子|毛毯|\b(?:clothes|clothing|shirts?|coats?|jackets?|trousers|socks?|scarves|gloves?|blankets?)\b/i],
    ['drink', /咖啡|茶叶|茶葉|茶包|饮料|飲料|矿泉水|礦泉水|气泡水|氣泡水|牛奶|豆奶|果汁|啤酒|红酒|紅酒|\b(?:coffee|tea|drinks?|beverages?|milk|juice|beer|wine|water)\b/i],
    ['food', /苹果|蘋果|菠萝|菠蘿|香蕉|橙子|饼干|餅乾|薯片|面包|麵包|方便面|方便麵|大米|面粉|麵粉|食用油|罐头|罐頭|坚果|堅果|巧克力|燕麦|燕麥|食盐|食鹽|酱油|醬油|\b(?:food|snacks?|biscuits?|cookies?|chips|bread|rice|flour|nuts?|chocolate|apples?|pineapples?|bananas?|oats?)\b/i],
    ['kitchen', /锅|鍋|碗|餐具|筷子|勺子|水杯|保温杯|保溫杯|饭盒|飯盒|保鲜盒|保鮮盒|菜刀|砧板|\b(?:cookware|pots?|pans?|bowls?|plates?|cutlery|cups?|mugs?|lunch[ -]?boxes?)\b/i],
    ['book', /图书|圖書|书籍|書籍|小说|小說|绘本|繪本|教材|杂志|雜誌|\b(?:books?|novels?|magazines?|textbooks?)\b/i],
    ['stationery', /文具|笔记本|筆記本|铅笔|鉛筆|圆珠笔|圓珠筆|中性笔|中性筆|钢笔|鋼筆|橡皮|胶带|膠帶|订书机|訂書機|剪刀|便签|便簽|\b(?:stationery|notebooks?|pencils?|pens?|erasers?|tape|staplers?|scissors)\b/i],
    ['toy', /玩具|积木|積木|拼图|拼圖|手办|手辦|毛绒|毛絨|乐高|樂高|\b(?:toys?|lego|puzzles?|dolls?|plush)\b/i],
    ['plant', /绿植|綠植|花盆|盆栽|园艺|園藝|种子|種子|肥料|浇水壶|澆水壺|\b(?:plants?|planters?|gardening|seeds?|fertilizers?)\b/i],
  ];
  const categories = [
    ['medicine', /药品|藥品|医药|醫藥|医疗|醫療|保健|\b(?:medicine|health|medical)\b/i],
    ['firstaid', /防护|防護|急救|\b(?:first[ -]?aid|protection)\b/i],
    ['food', /食品|零食|粮油|糧油|干货|乾貨|食材|水果|调味|調味|\b(?:food|snacks?|groceries)\b/i],
    ['drink', /饮品|飲品|饮料|飲料|茶咖|\b(?:drinks?|beverages?)\b/i],
    ['fitness', /运动|運動|健身|体育|體育|\b(?:sports?|fitness)\b/i],
    ['outdoor', /户外|戶外|露营|露營|\b(?:outdoors?|camping)\b/i],
    ['tool', /工具|五金|维修|維修|\b(?:tools?|hardware)\b/i],
    ['cleaning', /清洁|清潔|清扫|清掃|家务|家務|\b(?:cleaning|housekeeping)\b/i],
    ['care', /个护|個護|护理|護理|洗护|洗護|日化|\b(?:personal[ -]?care|toiletries)\b/i],
    ['clothing', /衣物|服饰|服飾|服装|服裝|纺织|紡織|床品|\b(?:clothing|apparel|textiles)\b/i],
    ['kitchen', /厨房|廚房|餐厨|餐廚|\b(?:kitchen|cookware)\b/i],
    ['book', /书|書|阅读|閱讀|\b(?:books?|reading)\b/i],
    ['stationery', /文具|办公|辦公|\b(?:stationery|office)\b/i],
    ['electronics', /数码|數碼|电子|電子|电器|電器|设备|設備|\b(?:electronics|devices?|appliances?)\b/i],
    ['toy', /玩具|收藏|母婴|母嬰|\b(?:toys?|collectibles?|baby)\b/i],
    ['pet', /宠物|寵物|\b(?:pets?)\b/i],
    ['plant', /植物|园艺|園藝|\b(?:plants?|gardening)\b/i],
  ];
  const text = value => String(value ?? '').normalize('NFKC').slice(0, 1000);
  function classify(item = {}) {
    for (const value of [item.name, item.tags]) {
      const content = text(value);
      const found = rules.find(([, pattern]) => pattern.test(content));
      if (found) return found[0];
    }
    const category = text(item.category);
    return rules.find(([, pattern]) => pattern.test(category))?.[0]
      || categories.find(([, pattern]) => pattern.test(category))?.[0] || 'box';
  }
  function render(item, caption = false) {
    const key = classify(item);
    const [label, tone, paths] = drawings[key];
    // Every interpolated value here comes from the fixed definitions above.
    return `<span class="item-art item-art-${tone}" data-item-icon="${key}" aria-hidden="true"><span class="item-art-symbol"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round">${paths}</svg></span>${caption ? `<span class="item-art-caption">${label}</span>` : ''}</span>`;
  }
  return Object.freeze({classify, render});
})();
