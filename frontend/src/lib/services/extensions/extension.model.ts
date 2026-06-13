export interface Extension {
  id: string;
  name: string;
  description: string;
  code: string;
  createdAt: string;
  updatedAt: string;
}

export interface IndexExtensionModel extends Extension {
  runOrder: number;
  enabled: boolean;
}
