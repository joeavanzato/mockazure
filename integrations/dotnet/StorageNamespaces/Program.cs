using System.Net.Security;
using System.Security.Cryptography.X509Certificates;
using System.Text.Json;
using Azure.Core.Pipeline;
using Azure.Storage;
using Azure.Storage.Blobs;
using Azure.Storage.Files.Shares;
using System.Reflection;

string Option(string name)=>args[Array.IndexOf(args,name)+1];
var p=JsonDocument.Parse(File.ReadAllText(Option("--profile"))).RootElement;
string Value(string key)=>p.GetProperty(key).GetString()!;
var origins=new[]{new Uri(Value("blob_endpoint")),new Uri(Value("file_endpoint"))};
foreach(var origin in origins)if(origin.Scheme!="https"||!new[]{"localhost","127.0.0.1"}.Contains(origin.Host))throw new Exception("Local HTTPS required");
var ca=X509Certificate2.CreateFromPem(File.ReadAllText(Value("ca_file")));
var handler=new HttpClientHandler {UseProxy=false,AllowAutoRedirect=false};
handler.ServerCertificateCustomValidationCallback=(request,certificate,oldChain,errors)=>{
 if(certificate is null||errors.HasFlag(SslPolicyErrors.RemoteCertificateNameMismatch))return false;
 using var chain=new X509Chain();chain.ChainPolicy.TrustMode=X509ChainTrustMode.CustomRootTrust;
 chain.ChainPolicy.CustomTrustStore.Add(ca);chain.ChainPolicy.RevocationMode=X509RevocationMode.NoCheck;return chain.Build(certificate);
};
var transport=new HttpClientTransport(new HttpClient(new LocalOnly(origins,handler)));
var key=new StorageSharedKeyCredential(Value("account"),Value("key"));
var blobs=new BlobClientOptions(BlobClientOptions.ServiceVersion.V2025_11_05){Transport=transport};blobs.Retry.MaxRetries=0;
var files=new ShareClientOptions(ShareClientOptions.ServiceVersion.V2025_11_05){Transport=transport};files.Retry.MaxRetries=0;
var blob=new BlobServiceClient(origins[0],key,blobs);var file=new ShareServiceClient(origins[1],key,files);
var container=blob.GetBlobContainerClient("dotnet-evidence");await container.CreateAsync(metadata:new Dictionary<string,string>{{"writer","dotnet"}});
if((await container.GetPropertiesAsync()).Value.Metadata["writer"]!="dotnet")throw new Exception("Blob metadata differs");
var names=new List<string>();await foreach(var row in blob.GetBlobContainersAsync())names.Add(row.Name);
if(!names.Contains("dotnet-evidence"))throw new Exception("Container missing");
var share=file.GetShareClient("dotnet-evidence");await share.CreateAsync(new Azure.Storage.Files.Shares.Models.ShareCreateOptions{Metadata=new Dictionary<string,string>{{"writer","dotnet"}}});
if((await share.GetPropertiesAsync()).Value.Metadata["writer"]!="dotnet")throw new Exception("File metadata differs");
names.Clear();await foreach(var row in file.GetSharesAsync())names.Add(row.Name);
if(!names.Contains("dotnet-evidence"))throw new Exception("Share missing");
string Version(Type type)=>type.Assembly.GetCustomAttribute<AssemblyInformationalVersionAttribute>()!.InformationalVersion.Split('+')[0];
File.WriteAllText(Option("--report"),JsonSerializer.Serialize(new{passed=true,created=2,clientVersions=new Dictionary<string,string>{{"Azure.Storage.Blobs",Version(typeof(BlobServiceClient))},{"Azure.Storage.Files.Shares",Version(typeof(ShareServiceClient))}}}));

class LocalOnly(Uri[] origins,HttpMessageHandler handler):DelegatingHandler(handler){
 protected override Task<HttpResponseMessage> SendAsync(HttpRequestMessage request,CancellationToken token){if(!origins.Any(u=>request.RequestUri!.GetLeftPart(UriPartial.Authority)==u.GetLeftPart(UriPartial.Authority)))throw new Exception("Nonlocal request rejected");return base.SendAsync(request,token);}
}
