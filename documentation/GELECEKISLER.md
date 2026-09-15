# Python Prototipi: Gelecek Mimari Planı

## 1. Amaç ve Kapsam

Bu belge, `prototype/` klasöründeki Python prototipinin planlanan gelişimini
açıklar. pthread tabanlı C/C++ çalışma zamanı için gelecek işleri tanımlamaz.

Mevcut prototip, bir programı tek seferlik görevlerden oluşan sabit bir hiyerarşi
olarak modeller. Hedef mimari ise benzetilen her bilgisayarı kalıcı bir
çözümleme motoru olarak ele alır: program işlevlerini tekrar tekrar
değerlendirebilen, diğer bilgisayarlardan istek kabul edebilen, program çalışması
boyunca yararlı durumu saklayabilen ve güvenli olduğunda sonuçları yeniden
kullanabilen etkileşimli bir yürütme ortamı.

Temel hedefler şunlardır:

- aynı işlevi, bütün yürütme ortamını yeniden oluşturmadan farklı parametrelerle
  tekrar tekrar değerlendirmek;
- programcı önceden kaç bilgisayar bulunacağını bilemese de bir değerlendirmeyi
  gerçekten kullanılabilir olan bilgisayarlara göre dağıtmak;
- worker'ların işlev değerlendirmelerini bir API aracılığıyla alt worker'lara
  devretmesini ve sonuçlarını beklemesini sağlamak;
- mutasyon veya önbellekleme davranışını belirsiz hâle getirmeden bir program
  çalışması boyunca yararlı verileri ve değerlendirme geçmişini saklamak.

## 2. Mevcut Prototip

Mevcut yürütme yolu `Manager`'dan `Worker`'a, oradan da havuzdaki bir `Runner`'a
uzanır:

- `Manager`, kod içine sabitlenmiş bir görev ağacı oluşturur ve her görevi bir
  worker'a atar.
- Her görev; bir ana program çağrısını, isteğe bağlı alt görevleri ve isteğe
  bağlı bir orkestrasyon programını tanımlar.
- Bir worker, her yürütme aşaması için `RunnerPool` içinden geçici olarak bir
  runner kiralar.
- Runner, atanmış Python dosyasını tek seferlik bir alt süreç olarak başlatır,
  çıktısını yakalar ve havuza geri döner.
- Üst worker kendi ana görevini yürütür, alt görevler bitene kadar paylaşılan
  görev nesnelerini yoklar ve ardından sonuçları birleştirmek için ayrı bir
  orkestrasyon programı çalıştırır.
- Runner sağlık kontrolleri, yeniden denemeler, sıfırlamalar ve kullanılamayan
  runner'ların yönetimi temel kurtarma davranışını sağlar.

Bu nedenle görevler bir kez oluşturulur ve programları bir kez çalıştırılır.
Worker'lar alt worker'larına API kanalları sunmaz. Hiyerarşi, birbirinden işlev
değerlendirmesi isteyebilen kalıcı bilgisayarlardan oluşan bir ağ yerine görev
kayıtlarından oluşan bir ağaç olarak bulunur. Runner'lar başlangıç belleğini,
işlev çağrısı geçmişini, önbelleğe alınmış sonuçları veya özelleştirilmiş bir
yürütme amacını saklamaz.

## 3. Hedef Yürütme Modeli

### 3.1 Worker'lar ve Çözümleme Motorları

Bir worker, kendisine atanmış bilgisayarın ve alt worker'larının mantıksal
orkestratörü olmaya devam edecektir. Bilgisayarın kendisi ise bir görev için
oluşturulup sonra atılan bir alt süreç yerine kalıcı bir çözümleme motoruyla
temsil edilecektir.

Çözümleme motoru, Haskell benzeri etkileşimli bir program olarak düşünülebilir:
motor hayatta olduğu sürece çağıran taraf, bir API üzerinden belirli
parametrelerle bir işlevi değerlendirmesini tekrar tekrar isteyebilir. Motor,
çağıran tarafın bu hesabı kendisinin yapmasına gerek kalmadan sonucu döndürür.

Bir program çalışmasının başında manager, kullanılabilir bilgisayarları
inceleyecek ve şunları atayacaktır:

- worker hiyerarşisi;
- her worker'a bir çözümleme motoru;
- her motora bir başlangıç amacı ve başlangıç belleği.

Bir motor, bu atamayı program çalışması boyunca korur. Amacı bir kısıtlama değil,
tercihli bir eşleşmedir: istekler, amacı ve sakladığı veriler işleve en uygun
motora yönlendirilmelidir; ancak yerel geri dönüş gerektiğinde bir motor başka
işlevleri de değerlendirebilir.

Program kodu, hesaplama verilerine kıyasla küçüktür; bu nedenle her motor
programın kullandığı bütün işlevleri alabilir. Motorlar, başka işlevlere
erişimleri engellenerek değil, atanmış amaçları, bellekleri ve önbellekteki
kayıtları aracılığıyla özelleştirilir.

Hiyerarşi ve motor amaçları bir çalışma sırasında sabit kalır. Tek bir
değerlendirme, atanmış alt worker'ların yalnızca bir bölümünü kullanabilir.
Çalışma sona erdiğinde motor durumu, önbellekler, geçmişler ve amaç atamaları
temizlenir ya da motor açıkça yeniden atanır.

> **Karar:** Worker ve bilgisayar ayrı kavramlar olarak kalır. Bir worker,
> kalıcı çözümleme motorunu ve alt worker'larını yönetir.

## 4. Değerlendirme API'si ve Orkestrasyon

### 4.1 Üst-Alt İletişimi

Worker'lar, açıkça tanımlanmış bir üst-alt istek/yanıt API'si üzerinden iletişim
kuracaktır. Bir işlevi değerlendiren worker, eş zamanlı olarak birden fazla alt
istek gönderebilir, işin kendi payına düşen kısmını sürdürebilir ve sonuçları
birleştirmeden önce ilişkilendirilmiş yanıtları bekleyebilir.

Bir değerlendirmenin tamamlanması worker'ın atamasını sona erdirmez. Sonuç,
worker'ı başka bir işlev değerlendirmesi istemeye yönlendirebilir; böylece
hesaplama tek, sabit ve tek seferlik bir görev yerine bir API çağrıları dizisi
olarak ilerleyebilir.

İletişim mantıksal hiyerarşi içinde kalır: bir worker, rastgele motorları
adreslemek yerine değerlendirme isteklerini kendisine atanmış alt worker'lara
gönderir. Böylece sahiplik, iptal ve hata yayılımı hesaplamayla aynı yapıyı izler.

Her değerlendirme isteği kavramsal olarak şunları içerir:

- idempotency anahtarı olarak da kullanılan kararlı bir çağrı kimliği;
- istenen işlev ve işlevin kod sürümü;
- açıkça sağlanan bütün bağımsız değişkenler;
- atlanan her bağımsız değişken için bir sürüm seçici;
- son tarih ve iptal bağlamı.

Her yanıt, çağrı kimliğiyle ilişkilendirilir ve başarılı bir sonuç ya da
yapılandırılmış bir hata içerir. Kesin aktarım yöntemi, serileştirme biçimi ve
dışarıya sunulan söz dizimi daha sonraki uygulama kararlarıdır.

### 4.2 Sıralama, İptal ve Hata

Her çözümleme motoru, gelen istek kuyruğunu sıralı olarak işler. Paralel yürütme
motorlar arasında gerçekleşirken motor başına sıralı işleme; bellek
güncellemelerine, sürüm seçimine ve önbellek erişimine belirlenimci bir düzen
kazandırır.

İptal, hiyerarşide aşağı doğru yayılır. Bir üst değerlendirmenin iptal edilmesi
veya zaman aşımına uğraması, onun adına oluşturulmuş ve henüz tamamlanmamış bütün
alt istekleri iptal eder. Bir üst, başarısız olmuş veya iptal edilmiş bir altı
hiçbir zaman süresiz beklememelidir.

Bir alt motor başarısız olursa çalışma zamanı önce onu değiştirmeyi ve
kurtarmayı dener. Kurtarma denemeleri tükenirse üst, kendi motoru bunu
yapabiliyorsa ilgili bölümü yerel olarak değerlendirir. Yerel değerlendirme
uyumlu değilse üst, yapılandırılmış bir hata döndürür.

Durum değiştiren çağrılar, yinelenen mutasyonları önlemek için kararlı çağrı
kimliklerini kullanır. Bir motor daha önce tamamlanmış bir mutasyon kimliği
aldığında güncellemeyi yeniden uygulamak yerine kayıtlı sonucu döndürür.

## 5. Sürümlü Bellek ve Değerlendirme Kayıtları

### 5.1 Başlangıç ve Güncellenmiş Bellek

Her çözümleme motoru bir program çalışmasına atanmış başlangıç belleğiyle
başlar. API'ler bu bellekteki adlandırılmış değerleri açıkça okuyabilir veya
güncelleyebilir. Her güncelleme, kayıtlı tek değeri sessizce değiştirmek yerine
ilgili parametrenin geçmişine değişmez bir sürüm ekler.

Bir işlev çağrısı bir parametreyi atladığında çağıran taraf, saklanan hangi
sürümün kullanılacağını belirtmelidir. Örneğin `--recent` en yeni sürümü,
belirli bir sürüm kimliği ise geçmişteki belirli bir değeri seçer. Seçici
olmadan atlanan bir parametre hatadır. `--recent` gibi bir seçici, motor isteği
kabul ettiğinde somut bir sürüme çözümlenir; böylece sonraki güncellemeler
işlenmekte olan bir çağrının anlamını değiştiremez.

> **Değerlendirilen alternatif:** Atlanan bir bağımsız değişken, önceki
> değerlendirmedeki değeri sessizce yeniden kullanabilirdi. Bu yapışkan parametre
> modeli, veri bağımlılıklarını gizlediği ve eş zamanlı çağrıları belirsiz hâle
> getirdiği için seçilmemiştir. Önceki değerler kullanılabilir olmaya devam eder,
> ancak yalnızca açık bir sürüm seçici aracılığıyla.

> **Mutasyon kısıtı:** Motorlar zaman içinde API'ler aracılığıyla belleklerini
> güncelleyebilir; ancak her mutasyon adlandırılmalı, sürümlenmeli, sıralanmalı ve
> kaydedilmelidir. Motor, değerlendirmelere izlenmeyen değişken durum sunmamalıdır.

### 5.2 Sonuçları Önbelleğe Alma

Aynı belirlenimci işlevi aynı etkin girdilerle birden fazla kez değerlendirmek
gereksizdir. Bu nedenle her motor, mevcut program çalışması için yerel bir
önbellek tutar.

Bir değerlendirme önbelleği anahtarı şunları içerir:

- işlev kimliği ve kod sürümü;
- normalleştirilmiş bağımsız değişkenlerin eksiksiz kümesi;
- değerlendirmenin kullandığı her saklanan değerin somut sürümü.

Yalnızca belirlenimci işlev değerlendirmeleri önbelleğe alınır. Durum güncelleme
API'leri hiçbir zaman önbelleğe alınmaz. Aynı açık bağımsız değişkenlere ancak
farklı ilgili bellek sürümlerine sahip çağrılar farklı değerlendirmelerdir ve
bir sonucu paylaşamaz.

Motor ayrıca sıralı bir API ve durum güncelleme kaydı tutar. Bu kayıt,
hesaplamayı izlemek ve kurtarma yapmak için yararlıdır. Bir yedek motor özgün
başlangıç belleğini yükleyebilir ve belirlenimci güncellemeleri sırayla yeniden
oynatabilir. Önbelleğe alınmış değerlendirme sonuçları kurtarılmak yerine yeniden
hesaplanabilir.

## 6. Bölümleme Sözleşmeleri

### 6.1 Gerekçe

Bir programcı, bir program için kaç bilgisayarın kullanılabilir olacağını
önceden bilemez. Bölümleme sözleşmesi, bilgisayar sayısını sabitlemeden bir
değerlendirmenin parametrelerine göre nasıl bölünebileceğini açıklar.

Öneriyi motive eden örnek şudur:

\[
h(x,y_{0:N})=h(x,y_{0:a})+h(x,y_{b:N})
\]

Seçilen yorum, bitişik, çakışmayan ve yarı açık aralıkları kullanır: `[0:a)` ve
`[a:N)`. Bu yorumda ikinci sınır `b`, `a`'ya eşittir. Analiz aracı, iki parçalı
gösterimi yararlı olan herhangi bir sayıda çakışmayan bölüme genelleyebilir.

### 6.2 Sözleşmenin İçeriği

Bölümlenebilir bir işlevin etkin tek bir sözleşmesi vardır. Sözleşme şunları
tanımlar:

- hangi parametrenin bölümlenebileceği;
- çakışmayan parametre aralıkları üretmenin geçerli kuralı;
- kısmi sonuçların nasıl birleştirileceği;
- boş girdi mümkünse birleştiricinin birim değeri;
- sonuç sırasının korunmasının gerekip gerekmediği;
- birleşme özelliğinin kısmi sonuçların yeniden gruplanmasına izin verip
  vermediği;
- tahminî hesaplama maliyeti ve bölümleme ek yükü.

Bu indirgeme özellikleri sözleşmenin parçasıdır; çünkü toplama gibi bir
birleştirici güvenle yeniden gruplanabilirken başka birleştiriciler kaynak
aralıklarının kesin sırasını gerektirebilir.

> **Yerini yeni karara bırakan öneri:** Önceki bir sürüm, işlev başına birden
> fazla bölümleme sözleşmesine izin veriyordu. Etkin tasarım, bölümlenebilir her
> işlev için tam olarak bir sözleşme kullanır. Bu nedenle analiz aracı alternatif
> sözleşmeler arasında seçim yapmak yerine bölüm sayısını ve sınırları seçer.

### 6.3 Analiz Aracının Davranışı

Bölümleme analiz aracı her değerlendirmede girdi boyutunu, programa atanmış
sağlıklı alt motorları, beklenen hesaplama maliyetini ve devretme ile birleştirme
ek yükünü dikkate alır.

Analiz aracı şunları yapacaktır:

- işlevin sözleşmesi yoksa değerlendirmenin tamamını yerel tutmak;
- bölümlemenin çalışma süresini iyileştirmesi beklenmiyorsa değerlendirmeyi
  yerel tutmak;
- devretme yararlıysa geçerli bir aralığı N adet çakışmayan bölüme ayırmak;
- yalnızca yararlı bir kazanım sağlayacak sayıda atanmış alt motor kullanmak;
- mevcut kapasitenin izin verdiği bölümleri devretmek ve kalan kısmı istekte
  bulunan motorda hesaplamak;
- kısmi sonuçları sözleşmedeki sıralama ve cebirsel güvencelere göre
  birleştirmek.

Analiz aracı ideal bir bilgisayar sayısını beklemez ve yalnızca daha az
bilgisayar bulunduğu için normalde geçerli olan bir değerlendirmeyi reddetmez.

## 7. Uygulama Yol Haritası

### Aşama 1: Kalıcı Motor Yaşam Döngüsü

- Açık worker ve motor kimlikleri eklemek.
- Her worker'a bir program çalışması boyunca kalıcı bir motor vermek.
- Atama, başlatma, sıfırlama, değiştirme ve sonlandırma durumlarını tanımlamak.

### Aşama 2: Değerlendirme Kanalı

- Tek seferlik runner çağrısını, beklenebilir bir üst-alt istek/yanıt kanalıyla
  değiştirmek.
- Çağrı ilişkilendirmesi, yapılandırılmış sonuçlar ve hatalar, son tarihler ve
  aşağı doğru yayılan iptal davranışı eklemek.
- Alt motorlar arasında eş zamanlı çalışmaya izin verirken her motorun gelen
  isteklerini sıralı işlemek.

### Aşama 3: Sürümlü Durum ve Yeniden Kullanım

- Başlangıç belleği ve değişmez parametre sürüm geçmişleri eklemek.
- Atlanan bağımsız değişkenler için açık seçiciler gerektirmek.
- Sıralı mutasyon günlükleri, idempotency takibi ve yerel değerlendirme
  önbellekleri eklemek.
- Yedek motorları belirlenimci durum güncellemelerini yeniden oynatarak
  kurtarmak.

### Aşama 4: Bölümleme Sözleşmeleri ve Analizi

- Tek sözleşme gösterimini ve indirgeme güvencelerini tanımlamak.
- Kullanılabilir alt motorlar üzerinde maliyet duyarlı N parçalı bölümlemeyi
  uygulamak.
- Bölünemez işler ve yetersiz kapasite için yerel yürütme desteği sunmak.

### Aşama 5: Analiz Aracının Ürettiği Programlar

- Kod içine sabitlenmiş demo görev ağacını program başlangıcında yapılan
  analizle değiştirmek.
- Worker hiyerarşisini, çözümleme motoru amaçlarını ve başlangıç belleğini
  kullanılabilir bilgisayarlara göre atamak.
- Alt değerlendirmeleri ve sonuç orkestrasyonunu sözleşmeler ve yeni API
  üzerinden yürütmek.

### Aşama 6: Kurtarma ve Gözlemlenebilirlik

- Yeniden oynatmaya dayalı kurtarmayı ve yerel geri dönüş davranışını
  tamamlamak.
- Hataların üstleri süresiz bekletmek yerine yapılandırılmış hatalarla
  sonlanmasını sağlamak.
- Çağrıları, çözümlenmiş durum sürümlerini, önbellek kararlarını, bölümleme
  planlarını, yeniden denemeleri, iptalleri ve kurtarma adımlarını izlemek.

## 8. Gelecekteki Kabul Senaryoları

Gelecekteki uygulama en az şu senaryoları kapsamalıdır:

- aynı belirlenimci işlev, kod sürümü, bağımsız değişkenler ve durum sürümleri
  yerel bir önbellek isabeti üretir;
- ilgili bir durum sürümünün değişmesi hatalı bir önbellek isabetini önler;
- seçicisi olmayan atlanmış bir parametre reddedilir;
- `--recent` ve belirli bir sürüm kimliği amaçlanan saklanmış değerlere
  çözümlenir;
- N parçalı bölümleme, bölümlenmemiş değerlendirmeyle aynı sonucu üretir;
- yeniden gruplanamayan bir birleştirici, tanımlanmış aralık sırasını korur;
- bölümleme ek yükü sağlayacağı yarardan fazlaysa değerlendirme yerel kalır;
- yetersiz alt kapasitesi yararlı işi devreder ve kalan kısmı yerel olarak
  hesaplar;
- başarısız bir motor, başlangıç belleğinden ve sıralı güncelleme günlüğünden
  yeniden oluşturulur;
- tekrarlanan bir mutasyon çağrısı kimliği mutasyonu iki kez uygulamaz;
- kurtarılamayan bir alt değerlendirme yerel çalışmaya geri döner veya
  yapılandırılmış bir hata üretir;
- üst değerlendirmenin iptal edilmesi, tamamlanmamış bütün alt çağrıları iptal
  eder;
- program sonlandırılırken motor durumu ve önbellekler temizlenir veya açıkça
  yeniden atanır.

## 9. Ertelenen Uygulama Ayrıntıları

Bu öneri henüz şunları seçmez:

- istek aktarım yöntemini veya serileştirme biçimini;
- `--recent` örneğinin ötesindeki somut Python API ve CLI yazımını;
- önbellek boyutu sınırlarını ve çıkarma politikasını;
- maliyet ve bölümleme ek yükü tahminlerinin ilk ayarlama yöntemini.

Bu seçimler yukarıda tanımlanan yürütme, durum, bölümleme veya kurtarma
anlamlarını değiştirmez.
